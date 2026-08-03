#include "usb_msc.h"

#include <Arduino.h>
#include <USB.h>
#include <USBMSC.h>
#include <esp_system.h>

#include "config.h"
#include "recorder.h"
#include "storage.h"

// Why a reboot instead of hot-swapping?
//
// Two things want the SD card: our FATFS mount and the USB host. They cannot
// both write it safely, and esp_vfs_fat_sdcard_unmount() tears down the card
// handle that the mass-storage callbacks need, so there is no clean way to hand
// the card over in place. Rebooting into a dedicated handover mode sidesteps
// the whole problem: in that mode the firmware performs no file I/O at all, so
// the host is the only writer. RTC memory survives a soft restart, which is how
// the mode carries across.

#define BOOT_NORMAL 0x00
#define BOOT_MSC 0x4D53  // 'MS'

RTC_NOINIT_ATTR static uint32_t bootMode;

static USBMSC msc;
static bool handoverMode = false;
static volatile bool hostAttached = false;
static bool mscStarted = false;

static int32_t onWrite(uint32_t lba, uint32_t offset, uint8_t *buffer, uint32_t bufsize) {
  sdmmc_card_t *card = storageCard();
  if (!card) return -1;
  if (sdmmc_write_sectors(card, buffer, lba + offset / 512, bufsize / 512) != ESP_OK) return -1;
  return bufsize;
}

static int32_t onRead(uint32_t lba, uint32_t offset, void *buffer, uint32_t bufsize) {
  sdmmc_card_t *card = storageCard();
  if (!card) return -1;
  if (sdmmc_read_sectors(card, buffer, lba + offset / 512, bufsize / 512) != ESP_OK) return -1;
  return bufsize;
}

static bool onStartStop(uint8_t power_condition, bool start, bool load_eject) {
  Serial.printf("[usb] start=%d eject=%d\n", start, load_eject);
  return true;
}

static void usbEventHandler(void *arg, esp_event_base_t base, int32_t id, void *data) {
  if (base != ARDUINO_USB_EVENTS) return;
  if (id == ARDUINO_USB_STARTED_EVENT || id == ARDUINO_USB_RESUME_EVENT) {
    hostAttached = true;
  } else if (id == ARDUINO_USB_STOPPED_EVENT || id == ARDUINO_USB_SUSPEND_EVENT) {
    hostAttached = false;
  }
}

bool usbMscActive() { return handoverMode; }

void usbMscBegin() {
  // A cold boot leaves RTC memory as garbage, so anything that is not exactly
  // BOOT_MSC counts as a normal start.
  esp_reset_reason_t reason = esp_reset_reason();
  if (reason == ESP_RST_POWERON || reason == ESP_RST_BROWNOUT) {
    bootMode = BOOT_NORMAL;
  }
  handoverMode = (bootMode == BOOT_MSC);
  bootMode = BOOT_NORMAL;  // one-shot: never get stuck in handover

  USB.onEvent(usbEventHandler);

  if (handoverMode) {
    sdmmc_card_t *card = storageCard();
    if (!card) {
      Serial.println("[usb] handover requested but no card — continuing as normal");
      handoverMode = false;
      USB.begin();
      return;
    }

    msc.vendorID("NotPlaud");
    msc.productID("Recorder");
    msc.productRevision("1.0");
    msc.onRead(onRead);
    msc.onWrite(onWrite);
    msc.onStartStop(onStartStop);
    msc.mediaPresent(true);
    msc.begin(card->csd.capacity, card->csd.sector_size);
    mscStarted = true;
    Serial.println("[usb] handover mode — card exposed to the computer");
  }

  USB.begin();
}

void usbMscLoop() {
  // USB suspend/resume events fire briefly during normal operation, so both
  // transitions are debounced: the state has to hold for a while before we act.
  static uint32_t stableSince = 0;
  static bool lastAttached = false;

  if (hostAttached != lastAttached) {
    lastAttached = hostAttached;
    stableSince = millis();
    return;
  }
  if (stableSince == 0) stableSince = millis();

  if (handoverMode) {
    if (!hostAttached && mscStarted && millis() - stableSince > 1500) {
      Serial.println("[usb] host gone — restarting as recorder");
      bootMode = BOOT_NORMAL;
      delay(50);
      esp_restart();
    }
    return;
  }

  // Normal mode: a host showing up means the user wants their files.
  if (hostAttached && millis() - stableSince > 1200) {
    if (recorderActive()) {
      Serial.println("[usb] plugged in — finishing the current recording first");
      recorderStop();
    }
    Serial.println("[usb] rebooting into handover mode");
    bootMode = BOOT_MSC;
    delay(100);
    esp_restart();
  }
}
