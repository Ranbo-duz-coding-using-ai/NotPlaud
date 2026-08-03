// NotPlaud — ESP32-S3 pocket recorder
//
// Three mechanical key switches:
//   RECORD    short press  -> start / stop recording
//   PAUSE     short press  -> pause / resume the current recording
//   TRANSMIT  short press  -> upload everything not sent yet over WiFi
//             long press   -> re-advertise over Bluetooth so the app can find us
//
// Plug into a computer with a data cable and the SD card is handed over as a
// USB drive instead. See README.md for wiring and flashing.

#include <Arduino.h>

#include "ble_config.h"
#include "config.h"
#include "mic_array.h"
#include "net_upload.h"
#include "recorder.h"
#include "storage.h"
#include "usb_msc.h"

// ---------------------------------------------------------------------------
// Status LED
// ---------------------------------------------------------------------------

enum LedState {
  LED_BOOT,
  LED_IDLE,
  LED_RECORDING,
  LED_PAUSED,
  LED_UPLOADING,
  LED_USB,
  LED_ERROR,
};

static LedState ledState = LED_BOOT;

static void ledSet(uint8_t r, uint8_t g, uint8_t b) {
#if LED_RGB_PIN >= 0
  neopixelWrite(LED_RGB_PIN, r, g, b);
#elif LED_SIMPLE_PIN >= 0
  digitalWrite(LED_SIMPLE_PIN, (r || g || b) ? HIGH : LOW);
#else
  (void)r; (void)g; (void)b;
#endif
}

static void ledUpdate() {
  const uint32_t now = millis();
  const bool slowBlink = (now / 600) % 2;
  const bool fastBlink = (now / 180) % 2;

  switch (ledState) {
    case LED_RECORDING: {
      // Brightness tracks the mic level so you can see it is hearing you.
      uint8_t level = (uint8_t)(20 + micArrayLevel() * 200.0f);
      ledSet(level, 0, 0);
      break;
    }
    case LED_PAUSED:
      ledSet(slowBlink ? 60 : 0, slowBlink ? 40 : 0, 0);
      break;
    case LED_UPLOADING:
      ledSet(0, 0, fastBlink ? 90 : 0);
      break;
    case LED_USB:
      ledSet(0, 40, 60);
      break;
    case LED_ERROR:
      ledSet(fastBlink ? 120 : 0, 0, 0);
      break;
    case LED_BOOT:
      ledSet(30, 30, 0);
      break;
    case LED_IDLE:
    default:
      // Dim green pulse when there is nothing waiting, amber when files are
      // still sitting on the card unsent.
      ledSet(0, slowBlink ? 12 : 4, 0);
      break;
  }
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

struct Button {
  uint8_t pin;
  bool lastReading;
  bool stableState;
  uint32_t lastChange;
  uint32_t pressedAt;
  bool longFired;
};

static Button btnRecord{BTN_RECORD, true, true, 0, 0, false};
static Button btnPause{BTN_PAUSE, true, true, 0, 0, false};
static Button btnTransmit{BTN_TRANSMIT, true, true, 0, 0, false};

enum PressKind { PRESS_NONE, PRESS_SHORT, PRESS_LONG };

static PressKind pollButton(Button &button) {
  bool reading = digitalRead(button.pin);  // pull-up: LOW while pressed
  uint32_t now = millis();

  if (reading != button.lastReading) {
    button.lastReading = reading;
    button.lastChange = now;
    return PRESS_NONE;
  }
  if (now - button.lastChange < DEBOUNCE_MS) return PRESS_NONE;

  if (reading != button.stableState) {
    button.stableState = reading;
    if (reading == LOW) {
      button.pressedAt = now;
      button.longFired = false;
    } else if (!button.longFired) {
      return PRESS_SHORT;  // released before the long-press threshold
    }
    return PRESS_NONE;
  }

  // Held down long enough — fire once, without waiting for the release.
  if (reading == LOW && !button.longFired && now - button.pressedAt >= LONG_PRESS_MS) {
    button.longFired = true;
    return PRESS_LONG;
  }
  return PRESS_NONE;
}

// ---------------------------------------------------------------------------
// Clock
// ---------------------------------------------------------------------------

// Unix time, derived from whatever the app or NTP last told us plus the time
// elapsed since. Returns 0 when the clock has never been set, in which case
// recordings get boot-relative names and the desktop app dates them on arrival.
uint32_t deviceEpochNow() {
  if (gConfig.epochAtSync == 0) return 0;
  return gConfig.epochAtSync + (millis() - gConfig.millisAtSync) / 1000;
}

// ---------------------------------------------------------------------------
// Audio pump
// ---------------------------------------------------------------------------

static int16_t audioBlock[I2S_FRAMES_PER_READ];

static void pumpAudio() {
  size_t got = micArrayRead(audioBlock, I2S_FRAMES_PER_READ);
  if (got == 0) return;
  recorderWrite(audioBlock, got);
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

static void toggleRecording() {
  if (recorderActive()) {
    recorderStop();
    ledState = LED_IDLE;
    bleConfigSetStatus("idle");
  } else {
    if (recorderStart(deviceEpochNow())) {
      ledState = LED_RECORDING;
      bleConfigSetStatus("recording");
    } else {
      ledState = LED_ERROR;
      bleConfigSetStatus("error:cannot-record");
    }
  }
}

static void togglePause() {
  if (!recorderActive()) return;
  if (recorderPaused()) {
    recorderResume();
    ledState = LED_RECORDING;
    bleConfigSetStatus("recording");
  } else {
    recorderPause();
    ledState = LED_PAUSED;
    bleConfigSetStatus("paused");
  }
}

static void onUploadProgress(int index, int total, const char *name) {
  Serial.printf("[upload] %d/%d %s\n", index, total, name);
  char status[64];
  snprintf(status, sizeof(status), "uploading %d/%d", index, total);
  bleConfigSetStatus(status);
  ledUpdate();
}

static void transmitAll() {
  // Recording and uploading at once would fight over the card and the radio.
  bool wasRecording = recorderActive();
  if (wasRecording) {
    Serial.println("[main] stopping recording before upload");
    recorderStop();
  }

  ledState = LED_UPLOADING;
  ledUpdate();
  bleConfigSetStatus("uploading");

  UploadResult result = netUploadAll(onUploadProgress);

  char status[96];
  snprintf(status, sizeof(status), "%s", result.message);
  bleConfigSetStatus(status);
  ledState = result.wifiOk ? LED_IDLE : LED_ERROR;
}

// ---------------------------------------------------------------------------
// Setup / loop
// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[main] NotPlaud starting");

#if LED_RGB_PIN >= 0
  pinMode(LED_RGB_PIN, OUTPUT);
#elif LED_SIMPLE_PIN >= 0
  pinMode(LED_SIMPLE_PIN, OUTPUT);
#endif
  ledState = LED_BOOT;
  ledUpdate();

  pinMode(BTN_RECORD, INPUT_PULLUP);
  pinMode(BTN_PAUSE, INPUT_PULLUP);
  pinMode(BTN_TRANSMIT, INPUT_PULLUP);

  configLoad();

  if (!storageBegin()) {
    Serial.println("[main] SD card unavailable — recording disabled");
    ledState = LED_ERROR;
  }

  // Decides whether this boot is a normal one or a USB handover, so it has to
  // run after the card is up but before we start capturing audio.
  usbMscBegin();

  if (usbMscActive()) {
    ledState = LED_USB;
    Serial.println("[main] USB handover mode — recording is off until unplugged");
    return;  // loop() only services USB in this mode
  }

  if (!micArrayBegin()) {
    Serial.println("[main] microphone array failed to start");
    ledState = LED_ERROR;
  }
  micArraySetMode(micModeFromName(gConfig.mode));

  bleConfigBegin();
  bleConfigSetStatus("idle");

  if (ledState != LED_ERROR) ledState = LED_IDLE;

  Serial.printf("[main] ready — %d recording(s) waiting to upload\n", storageUnsentCount());
}

void loop() {
  usbMscLoop();

  if (usbMscActive()) {
    ledState = LED_USB;
    ledUpdate();
    delay(50);
    return;
  }

  // Audio first: the I2S buffers are what we cannot afford to starve.
  pumpAudio();

  switch (pollButton(btnRecord)) {
    case PRESS_SHORT: toggleRecording(); break;
    case PRESS_LONG: break;
    default: break;
  }

  switch (pollButton(btnPause)) {
    case PRESS_SHORT: togglePause(); break;
    default: break;
  }

  switch (pollButton(btnTransmit)) {
    case PRESS_SHORT: transmitAll(); break;
    case PRESS_LONG:
      Serial.println("[main] re-advertising over Bluetooth");
      bleConfigAdvertise();
      bleConfigSetStatus("pairing");
      break;
    default: break;
  }

  bleConfigLoop();
  ledUpdate();
}
