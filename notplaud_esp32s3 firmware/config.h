// NotPlaud ESP32-S3 — hardware configuration.
//
// Every pin below is a wiring choice, not a fixed requirement. Change them to
// match how you actually built the device, then reflash. The only constraints:
//   * SD_MMC CLK/CMD/D0 must be on pins that support the SDMMC peripheral.
//   * The two I2S buses must not share BCLK/WS pins.
//   * GPIO 19/20 are the native USB D-/D+ pins on the S3 — leave them alone.

#pragma once

// ---------------------------------------------------------------------------
// Audio
// ---------------------------------------------------------------------------

// 16 kHz mono is what Whisper wants, and it keeps files small enough that a
// long lecture still uploads over WiFi in a reasonable time.
#define SAMPLE_RATE 16000
#define BITS_PER_SAMPLE 16
#define AUDIO_CHANNELS 1

// Frames pulled from each I2S bus per read. 256 frames @16 kHz = 16 ms.
#define I2S_FRAMES_PER_READ 256

// MEMS mics (INMP441 / ICS-43434 / SPH0645) deliver 24-bit samples
// left-justified inside a 32-bit slot. This shift converts to ~16-bit and sets
// the baseline gain. Raise for quieter rooms, lower if you hear clipping.
#define MIC_SHIFT 11

// I2S bus A — microphones 1 (left) and 2 (right)
#define I2S_A_BCLK 4
#define I2S_A_WS   5
#define I2S_A_DIN  6

// I2S bus B — microphones 3 (left) and 4 (right)
#define I2S_B_BCLK 7
#define I2S_B_WS   15
#define I2S_B_DIN  16

// Physical spacing between adjacent mics, in metres. Used by the
// voice-isolation beamformer to work out inter-mic delays. Measure your own
// board and put the real number here — a wrong value makes beamforming worse
// than plain averaging.
#define MIC_SPACING_M 0.035f

// ---------------------------------------------------------------------------
// SD card (SDMMC, 1-bit mode — fewer pins, plenty fast for 16 kHz mono)
// ---------------------------------------------------------------------------

#define SD_CLK 36
#define SD_CMD 35
#define SD_D0  37

#define REC_DIR   "/sdcard/notplaud/recordings"
#define SENT_FILE "/sdcard/notplaud/sent.txt"
#define ID_FILE   "/sdcard/notplaud.id"   // marker the desktop app looks for

// ---------------------------------------------------------------------------
// Buttons — mechanical key switches, wired to ground, using internal pull-ups
// ---------------------------------------------------------------------------

// Short press: start / stop recording.
#define BTN_RECORD 1
// Short press: pause / resume the current recording.
#define BTN_PAUSE 2
// Short press: upload everything not yet sent.  Long press: re-advertise BLE.
#define BTN_TRANSMIT 42

#define DEBOUNCE_MS 35
#define LONG_PRESS_MS 1500

// ---------------------------------------------------------------------------
// Status LED
// ---------------------------------------------------------------------------

// Most ESP32-S3 dev boards have an addressable RGB LED. Set to -1 if yours
// does not, and the firmware will fall back to a plain LED on LED_SIMPLE_PIN.
#define LED_RGB_PIN 48
#define LED_SIMPLE_PIN -1

// ---------------------------------------------------------------------------
// Battery monitoring (optional — set to -1 to disable)
// ---------------------------------------------------------------------------

#define BATTERY_ADC_PIN 9
#define BATTERY_DIVIDER 2.0f      // 2:1 resistor divider from the cell
#define BATTERY_MIN_V 3.30f
#define BATTERY_MAX_V 4.20f

// ---------------------------------------------------------------------------
// Behaviour
// ---------------------------------------------------------------------------

// Roll to a new file past this size so one long session is not a single
// enormous upload. 0 disables splitting.
#define MAX_FILE_BYTES (256UL * 1024UL * 1024UL)

// Stop recording and warn when the card is nearly full.
#define MIN_FREE_BYTES (8UL * 1024UL * 1024UL)

// How long to wait for WiFi before giving up on an upload run.
#define WIFI_TIMEOUT_MS 20000

// Upload chunk size. 4 KB keeps RAM use low and plays nicely with the
// desktop app's streaming reader.
#define UPLOAD_CHUNK 4096

// BLE identity. The desktop app scans for names starting with "NotPlaud".
#define BLE_NAME_PREFIX "NotPlaud"
#define BLE_SERVICE_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define BLE_CHAR_CONFIG_UUID "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
#define BLE_CHAR_STATUS_UUID "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

// Config wire format version the app sends (see device_comm.py).
#define CONFIG_VERSION 2
