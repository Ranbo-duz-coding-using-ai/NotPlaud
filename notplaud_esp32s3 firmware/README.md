# NotPlaud — ESP32-S3 firmware

Pocket recorder: four I2S microphones, three mechanical key switches, an SD
card, Bluetooth for settings, WiFi for uploads, and USB for bulk transfer.

## Buttons

You said three switches. Here is how they map — note that "start" and "stop"
are the same switch, because a single toggle is far less error-prone in a
pocket than two separate keys:

| Switch     | Short press                          | Long press (1.5 s)              |
| ---------- | ------------------------------------ | ------------------------------- |
| `RECORD`   | Start recording / stop and save      | —                               |
| `PAUSE`    | Pause / resume the current recording | —                               |
| `TRANSMIT` | Upload everything not yet sent       | Re-advertise over Bluetooth     |

If you would rather have a dedicated stop key, change the `BTN_PAUSE` handler
in `notplaud_esp32s3.ino` to call `recorderStop()` directly.

## Status LED

| Colour                 | Meaning                                  |
| ---------------------- | ---------------------------------------- |
| Dim green pulse        | Idle, ready                              |
| Red (brightness = level) | Recording — brightness follows the mic  |
| Amber blink            | Paused                                   |
| Blue blink             | Uploading over WiFi                      |
| Steady cyan            | USB handover — card is mounted on the PC |
| Fast red blink         | Error (no SD card, card full, I2S failed)|

## Wiring

All pins live in `config.h`. Change them to match your build; nothing else
needs editing.

### Microphones (INMP441 / ICS-43434 / SPH0645)

Two I2S buses, two mics each. On each bus, wire one mic's `L/R` pin to GND
(left channel) and the other's to VDD (right channel) — that is what puts two
mics on one bus.

| Signal | Bus A (mics 1 & 2) | Bus B (mics 3 & 4) |
| ------ | ------------------ | ------------------ |
| BCLK   | GPIO 4             | GPIO 7             |
| WS/LRCL| GPIO 5             | GPIO 15            |
| SD/DOUT| GPIO 6             | GPIO 16            |

Set `MIC_SPACING_M` to the real centre-to-centre spacing of your mics. The
voice-isolation mode uses it to compute inter-mic delays, and a wrong value
makes that mode worse than plain averaging.

### SD card (SDMMC, 1-bit)

| Signal | GPIO |
| ------ | ---- |
| CLK    | 36   |
| CMD    | 35   |
| D0     | 37   |

Format the card as FAT32 before first use.

### Buttons

Each switch goes between its GPIO and GND. Internal pull-ups are enabled in
firmware, so no external resistors are needed.

| Switch   | GPIO |
| -------- | ---- |
| RECORD   | 1    |
| PAUSE    | 2    |
| TRANSMIT | 42   |

### Battery

A single Li-ion cell through a charge/protection board (TP4056 or similar) into
the board's battery input. To read the level, wire the cell through a 2:1
divider into `BATTERY_ADC_PIN` (GPIO 9). Never feed raw cell voltage into a GPIO.

## Capture modes

Pushed from the app's Device tab over Bluetooth, applied on the next audio block.

- **Standard** — plain average of all four mics. Averaging four mics already
  buys roughly 6 dB against diffuse room noise, because speech is correlated
  across the array and noise is not.
- **Wide Spectrum** — same average with extra gain and no gating, so ambience
  and distant speakers survive. Use it when you want to hear the whole room.
- **Voice Isolation** — time-aligned (delay-and-sum) mix, a ~120 Hz high-pass to
  kill HVAC rumble and handling noise, and a noise-floor-tracking gate that
  ducks anything sitting near the floor. Fast attack so word onsets are not
  clipped, slow release so it does not chatter between words.

A candid note on beamforming: with mics ~3.5 cm apart at 16 kHz, the delay
between adjacent mics is well under one sample, so the steering itself does very
little at the default broadside angle. The real gains in this mode come from the
four-mic average, the high-pass, and the gate. If you want genuine directivity,
you need a wider array or a higher sample rate — `VOICE_STEER_DEG` is there to
experiment with once you do.

## Building and flashing

### Arduino IDE

1. Install the **esp32** board package by Espressif, **version 3.0.0 or newer**
   (this firmware uses the ESP-IDF 5.x `i2s_std` driver, which 2.x does not have).
2. Open `notplaud_esp32s3.ino`.
3. Board: **ESP32S3 Dev Module**.
4. Set these under Tools:
   - USB CDC On Boot: **Enabled**
   - USB Mode: **USB-OTG (TinyUSB)**  ← required for the SD-card-as-drive feature
   - Partition Scheme: **Huge APP (3MB No OTA/1MB SPIFFS)** (BLE + WiFi is a big binary)
   - PSRAM: **OPI PSRAM** if your module has it
5. Upload.

### arduino-cli

```bash
arduino-cli core install esp32:esp32
```

```bash
arduino-cli compile --fqbn esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,PartitionScheme=huge_app firmware/notplaud_esp32s3
```

```bash
arduino-cli upload -p /dev/cu.usbmodem101 --fqbn esp32:esp32:esp32s3 firmware/notplaud_esp32s3
```

No external libraries are needed — `BLEDevice`, `WiFi`, `USBMSC`, and
`Preferences` all ship with the ESP32 core.

## Autostart on boot

There is nothing to configure. Microcontrollers are not like a Raspberry Pi:
the flashed program *is* the boot sequence. On power-up the ESP32-S3 bootloader
loads the app partition and calls `setup()`, then `loop()` forever. Flash once
and the device records on battery with no computer attached.

What that means practically:

- **Power on → ready to record.** Press RECORD; no host, no app, no pairing.
- **Settings survive power loss.** Capture mode, WiFi credentials, the upload
  address, and the pairing token are written to NVS (flash) by `configSave()`
  and reloaded by `configLoad()` in `setup()`.
- **The clock does not survive.** There is no battery-backed RTC, so after a
  power cycle the device does not know the time until the app pushes settings
  over Bluetooth or it reaches NTP during an upload. Recordings made before
  then are named `session_boot<seconds>.wav`; the app dates those from when
  they arrive instead of from the filename.
- **To recover from a bad flash**, hold BOOT while tapping RESET to force the
  ROM bootloader, then re-upload.

If you want it to start recording the instant it powers on, add
`recorderStart(deviceEpochNow());` at the end of `setup()`.

## Getting recordings off the device

**Over WiFi.** Press TRANSMIT. The device joins the network the app told it
about, POSTs every unsent file to `http://<computer>:8788/upload`, and marks
each one in `notplaud/sent.txt` so it is never sent twice. Then it drops WiFi to
save battery. Files stay on the card until you delete them.

**Over USB.** Plug into the computer with a *data* cable. The device finishes
the current recording, reboots into handover mode, and the card appears as a
removable drive. Open the app's Settings → Transfers and press *Import from
USB*. Unplug and it reboots back into recorder mode.

Why a reboot? Two writers on one FAT volume corrupts it, and unmounting the
filesystem in place also destroys the card handle the USB layer needs. Rebooting
into a mode where the firmware performs no file I/O at all makes the host the
only writer, which is the only genuinely safe arrangement.

## First-time setup

1. Flash the firmware and insert a FAT32 card.
2. Power on. The device advertises as `NotPlaud Node` over Bluetooth.
3. Open the desktop app → **Settings → WiFi networks** and add your network.
4. Press **Push to device now**. That single write hands over the capture mode,
   WiFi credentials, upload address, pairing token, and current time.
5. Press RECORD, talk, press RECORD again, then press TRANSMIT.

## Things worth knowing

- The WAV header is rewritten roughly once a second while recording, so a dead
  battery mid-session leaves a playable file rather than a zero-length one.
- Recording stops automatically when the card drops below `MIN_FREE_BYTES` (8 MB).
- Files roll over at `MAX_FILE_BYTES` (256 MB, about 2.3 hours at 16 kHz mono)
  so one long session is not a single enormous upload.
- 16 kHz mono is roughly 32 KB/s — about 115 MB per hour.
- This firmware has been written against the ESP32-S3 API but **has not been run
  on hardware**. Expect to adjust pin assignments for your board, and check
  `MIC_SHIFT` against your microphones' actual output level (raise it if
  recordings clip, lower it if they are too quiet).
