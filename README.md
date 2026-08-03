# NotPlaud

**A DIY, local-first alternative to the Plaud AI recorder.** An ESP32-S3 pill in
your pocket records; a desktop app turns it into a written note. The AI runs on
your own machine by default — no subscription, no cloud, nothing leaves your
computer unless you decide it should.

> **Status:** the desktop app is working and tested. The firmware is complete
> but **has not yet been run on real hardware** — see [Project status](#project-status).

---

## Why

Commercial AI recorders cost around $160 plus a subscription, and every word you
record goes to somebody else's server. NotPlaud does the same job with about $30
of parts, and by default your audio never leaves your machine.

## What it does

- **Records** on a pocket device with a 4-microphone array, or straight from
  your computer for Zoom/Meet/Teams calls.
- **Transcribes** locally with Whisper (`faster-whisper`).
- **Writes the note** locally with any GGUF model (`llama-cpp-python`) — a
  structured summary with key points, decisions, action items, and open
  questions, tuned by preset.
- **Organises** everything into folders with drag-and-drop, search, PDF export,
  and per-note detail levels.
- **Optionally** uses OpenAI, Google, or Claude instead, if you would rather.

---

## How it works

```
┌─────────────────┐   Bluetooth LE    ┌────────────────────────────┐
│  ESP32-S3 pill  │ ◀──── config ──── │      Desktop app           │
│                 │                   │                            │
│  4 × I2S mics   │ ──── WiFi ──────▶ │  ingest server :8788       │
│  3 key switches │     upload        │           │                │
│  SD card        │                   │           ▼                │
│  Li-ion battery │ ──── USB ───────▶ │  transcribe (local Whisper)│
└─────────────────┘   mass storage    │           │                │
                                      │           ▼                │
                                      │  summarize (local GGUF)    │
                                      │           │                │
                                      │           ▼                │
                                      │  note + PDF + audio        │
                                      └────────────────────────────┘
```

1. **Capture.** Press RECORD on the device. Four mics are mixed to mono 16 kHz
   using the capture mode you selected, and written as WAV to the SD card. The
   device works entirely standalone — no phone, no computer, no pairing.
2. **Transfer.** Press TRANSMIT and it joins your WiFi and uploads everything it
   has not sent before, then drops WiFi to save battery. Or plug in USB and the
   card mounts as a normal drive.
3. **Transcribe.** The app picks up new audio and runs Whisper on a background
   worker, so the window stays responsive even though local models are slow.
4. **Summarise.** The transcript plus your chosen preset ("Conference", "Online
   Class"…) and detail level go to the summary model, which returns a structured
   note with an AI-generated title.
5. **Read.** The note appears on the Home tab, dated and titled, with the
   original audio one click away.

### Capture modes

| Mode | What it does | Good for |
|---|---|---|
| **Standard** | Plain 4-mic average | Desks, small rooms, voice notes |
| **Wide Spectrum** | Higher gain, no gating | Group discussions, room ambience |
| **Voice Isolation** | Time-aligned mix + 120 Hz high-pass + noise gate | Lecture halls, noisy rooms |

Averaging four mics buys roughly 6 dB against diffuse room noise on its own,
because speech is correlated across the array and noise is not.

### Note presets

Conference · Online Class · Interview · Voice Memo · General Notes — each sends
a different instruction to the summary model. Detail runs Low → Medium → High →
Ultra, set globally or per note, and cached per level so switching back is
instant.

---

## Hardware

| Part | Notes | Approx. |
|---|---|---|
| ESP32-S3 dev board | Needs native USB; 8 MB flash or more | $8–12 |
| 4 × I2S MEMS mics | INMP441, ICS-43434, or SPH0645 | $8 |
| microSD card + slot | FAT32 formatted | $5 |
| 3 × mechanical key switches | Any Cherry-style switch | $3 |
| Li-ion cell + TP4056 charger | 500–1000 mAh | $6 |

Full wiring tables are in
[`firmware/notplaud_esp32s3/README.md`](firmware/notplaud_esp32s3/README.md).
Every pin is configurable in `config.h`.

### Buttons

| Switch | Short press | Long press |
|---|---|---|
| RECORD | Start / stop recording | — |
| PAUSE | Pause / resume | — |
| TRANSMIT | Upload everything unsent | Re-advertise over Bluetooth |

---

## Install

Requires **Python 3.10+**.

```bash
git clone https://github.com/YOUR_USERNAME/notplaud.git
```

```bash
cd notplaud && python3 -m venv .venv
```

**macOS / Linux:**

```bash
.venv/bin/pip install -r notplaud_app/requirements.txt
```

**Windows:**

```bat
.venv\Scripts\pip install -r notplaud_app\requirements.txt
```

Linux also needs system packages for the window backend:

```bash
sudo apt install python3-gi gir1.2-webkit2-4.1 libcairo2-dev
```

### Run

**macOS / Linux:**

```bash
.venv/bin/python notplaud_app/desktop.py
```

**Windows:** double-click `NotPlaud.bat`, or:

```bat
.venv\Scripts\python notplaud_app\desktop.py
```

macOS users can also double-click `NotPlaud.command`, which creates the
virtualenv on first run.

### Flash the firmware

Install the **esp32** board package by Espressif, **version 3.0.0 or newer**
(this firmware uses the ESP-IDF 5.x `i2s_std` driver). Open
`firmware/notplaud_esp32s3/notplaud_esp32s3.ino` in the Arduino IDE, select
**ESP32S3 Dev Module**, set **USB Mode: USB-OTG (TinyUSB)** and **Partition
Scheme: Huge APP**, and upload. No external libraries needed.

Nothing needs configuring to make it start on boot — on a microcontroller the
flashed program *is* the boot sequence. Power on and it is ready to record.

---

## Platform support

| | macOS | Windows | Linux |
|---|---|---|---|
| Desktop app | ✅ tested | ⚠️ untested | ⚠️ untested |
| Local Whisper / GGUF | ✅ | ✅ | ✅ |
| Bluetooth config push | ✅ | ⚠️ untested | ⚠️ untested |
| WiFi upload from device | ✅ | ✅ | ✅ |
| USB import | ✅ | ⚠️ untested | ⚠️ untested |
| **Recording calls in-app** | ❌ see below | ✅ expected | ⚠️ untested |

Cross-platform code paths exist for all three OSes (`netsh` on Windows,
`nmcli` on Linux, drive-letter scanning for USB), but only macOS has actually
been run. Bug reports from Windows and Linux users are very welcome.

### The one known gap: recording calls on macOS

The app renders in the OS's native web view. On macOS that is WebKit, which
**does not expose `navigator.mediaDevices` to embedded web views** unless the
host is a signed app bundle with microphone entitlements. So the **Calls tab
cannot capture computer audio on macOS today.** It fails with a clear message
rather than hanging.

Everything else — the device recorder, transcription, summaries, folders, PDF
export — is unaffected, and the ESP32 recording path does not touch this at all.

On Windows the web view is Edge WebView2, which is Chromium-based and does
support `getDisplayMedia`, so call recording is expected to work there.

**Workaround on macOS:** run `notplaud_app/app.py` and open
`http://127.0.0.1:8787` in Chrome or Edge to record calls. See issue tracker for
the planned fix (native capture via `sounddevice` + BlackHole).

---

## Privacy

- **Local by default.** Both models default to on-device. Nothing is uploaded
  unless you explicitly choose an API provider.
- **The UI server binds to `127.0.0.1`.** The only thing listening on your
  network is the upload receiver on port 8788, and all it accepts is an audio
  file carrying the pairing token your device was given.
- **Your data stays in `notplaud_app/data/`** — plain files. Back it up by
  copying the folder.
- **API keys and WiFi passwords** live in `data/app_state.json`, which is
  gitignored, and are masked (`********`) whenever sent to the UI layer. You can
  also leave keys blank and use environment variables instead.

---

## FAQ

**Do I need Chrome?**
No. The app uses whichever web engine your OS already ships — WebKit on macOS,
Edge WebView2 on Windows, webkit2gtk on Linux. The one exception is recording
calls on macOS, described above.

**Do I need an internet connection?**
Only to download models the first time. After that, everything runs offline.

**How good is local transcription?**
Whisper `base` is fine for clear speech and is fast. `small` or `medium` are
noticeably better on accents and noisy rooms, and proportionally slower.
`large-v3` is excellent but wants a decent GPU or a lot of patience.

**Which summary model should I use?**
A 7–8B instruct model in Q4_K_M is the sweet spot — comfortably runs on most devices
unless you are working on an absolute potato. It is recommended that you have
at least 8GB of RAM, with 16GB of RAM being the sweet spot. 

**How long does the battery last?**
Depends on your cell. 16 kHz mono is roughly 32 KB/s, so storage is rarely the
limit; the radios are. WiFi is only powered up during uploads, for that reason.

**How much space do recordings take?**
About 115 MB per hour. A 32 GB card holds roughly 270 hours.

**Can I use it without the hardware?**
Yes. The Calls tab and file import work standalone — the device is optional.

**Is my audio sent anywhere?**
Not unless you switch a model to an API provider. On the default settings, no
network requests leave your machine at all.

**Why is the first recording slow?**
The model loads into memory on first use and is cached afterwards.

**Can I change the note format?**
Yes — `build_summary_prompt()` in `notplaud_app/app.py` defines the structure,
and the presets are a list at the top of the same file.

**Why does the device stop recording when I plug in USB?**
Two writers on one FAT volume corrupts it. The device finishes the current
recording, reboots into handover mode, and hands the card to your computer.
Unplug and it reboots back into recorder mode.

---

## Project status

**Working and tested:** the desktop app end to end — device upload → note →
PDF/audio/rename/move/delete, background processing, settings, theming, WiFi
management, USB import.

**Written but not hardware-tested:** the ESP32-S3 firmware. It compiles against
the ESP32 Arduino core 3.x API, but has never run on a physical board. Expect to
adjust pin assignments in `config.h` for your build and tune `MIC_SHIFT` to your
microphones. Reports from anyone who builds one are hugely appreciated.

**A candid note on beamforming:** with mics ~3.5 cm apart at 16 kHz, the delay
between adjacent mics is well under one sample, so the steering in Voice
Isolation does very little at the default angle. The real gains in that mode come
from the 4-mic average, the high-pass, and the noise gate. Genuine directivity
needs a wider array or a higher sample rate.

## Contributing

Issues and pull requests welcome — particularly Windows and Linux testing,
hardware builds, and native audio capture to fix the macOS Calls gap.

## License

MIT — see [LICENSE](LICENSE).
