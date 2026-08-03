# NotPlaud

A DIY alternative to the Plaud pill: an ESP32-S3 recorder in your pocket, and a
desktop app that turns whatever it heard into a written note — with the AI
running **on your own machine** by default.

This folder is the desktop application. The recorder firmware is in
[`../firmware/notplaud_esp32s3`](../firmware/notplaud_esp32s3).

## Install

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r notplaud_app/requirements.txt
```

`faster-whisper` and `llama-cpp-python` are the two heavy ones. The app runs
without them — it just tells you what is missing instead of writing notes — so
if the install is slow you can start with the rest and add them later.

## Run

```bash
.venv/bin/python notplaud_app/desktop.py
```

That opens a native window. It is an application, not a web page: no browser,
no localhost URL to remember, and the "Browse…" buttons open the real macOS
file picker.

`app.py` also runs standalone as a headless service if you ever want that, but
`desktop.py` is the one you want day to day.

## The four tabs

**Home** — every note, newest first, dated and titled by the AI. Each note has a
⋯ menu to rename, download a PDF, view the raw transcription, play the original
audio, move it to a folder, change its detail level, or delete it. Drag a note
onto a folder in the sidebar to file it. Search covers titles, notes, and
transcripts.

**Device** — the capture mode your recorder uses (Standard / Wide Spectrum /
Voice Isolation) and the note style it should apply. Picking one pushes it
straight to the device over Bluetooth.

**Calls** — record Zoom, Meet, Teams, or anything else playing on this computer.
Pick computer audio, your mic, both, or a single app/window/tab. Your system's
share picker is what selects a specific app — tick "Share audio" in it. There is
a live level meter, and pause/resume mid-call.

**Settings** — everything below.

## Models: local by default

Both the summary and transcription models have the same shape:

- **On-device model (offline)** — nothing leaves your machine.
  - Summary: browse to a `.gguf` chat model (Llama, Qwen, Mistral…), run
    through `llama-cpp-python`.
  - Transcription: type a Whisper size (`tiny`, `base`, `small`, `medium`,
    `large-v3`) which downloads once and caches, or browse to a converted model
    folder. Runs through `faster-whisper`.
- **API key (cloud)** — pick OpenAI, Google, or Claude, choose a model, paste a
  key. You can also leave the key blank and set the environment variable
  instead, which keeps it out of the app's state file entirely.

Keys are stored in `data/app_state.json` and are masked (`********`) whenever
they are sent to the UI, so they never round-trip back through the browser layer.

**A good starting point:** transcription `base`, and a 7–8B instruct model in
Q4_K_M for summaries. On Apple Silicon that is comfortably real-time. Larger
Whisper models are noticeably more accurate on accented or noisy audio, and
noticeably slower.

Because local models are slow, transcription and summarisation run on a
background worker. The app stays responsive, notes show a "working…" badge, and
the list updates itself when each one finishes.

## Detail levels

Low / Medium / High / Ultra, chosen as cards in Settings the same way capture
modes are chosen on the Device tab. That is the default for new notes; any
single note can be switched from its ⋯ menu, and each level is cached per note
so flipping back is instant.

## Appearance

Light/dark switcher in the sidebar. The choice is saved to your settings and
also to `localStorage`, so the window opens in the right theme without a flash.

## WiFi and Bluetooth

Add the networks you use under **Settings → WiFi networks**. The app notices
which one this computer is on and pushes that network — plus the capture mode,
the upload address, a pairing token, and the current time — to the device over
Bluetooth in a single write. Turn off "Automatically push…" if you would rather
press the button yourself.

The device has no clock of its own, which is why the time is part of every push.

## Getting recordings across

**Over WiFi.** Press TRANSMIT on the device. It uploads everything it has not
sent before to a small LAN server the app runs on port 8788, then drops WiFi.
Only uploads carrying the pairing token are accepted, so a stray machine on the
same network cannot push files at you. Notes appear on their own, no sync press
needed.

The main UI server stays bound to `127.0.0.1`; port 8788 is the only thing
listening on the LAN, and all it can do is accept an audio file.

**Over USB.** Plug the device in with a data cable and its card mounts as a
drive. Settings → Transfers shows it; press *Import from USB*.

**Any folder.** Anything dropped into `data/incoming/` is picked up by *Sync
device*.

## Start automatically at login (macOS)

Copy the launch agent and load it:

```bash
cp notplaud_app/com.notplaud.app.plist ~/Library/LaunchAgents/
```

```bash
launchctl load -w ~/Library/LaunchAgents/com.notplaud.app.plist
```

Edit the paths inside the plist first if you moved the project. To stop it
launching at login:

```bash
launchctl unload -w ~/Library/LaunchAgents/com.notplaud.app.plist
```

On Windows, put a shortcut to `pythonw.exe notplaud_app\desktop.py` in
`shell:startup`. On Linux, drop a `.desktop` file in `~/.config/autostart/`.

## Where things live

| Path                    | What                                          |
| ----------------------- | --------------------------------------------- |
| `data/app_state.json`   | Notes, folders, settings                      |
| `data/audio/`           | Original recordings, named by note id         |
| `data/incoming/`        | Landing zone for device uploads               |
| `app.py`                | HTTP API, note pipeline, PDF export           |
| `desktop.py`            | Native window + file picker                   |
| `jobs.py`               | Background worker                             |
| `ingest_server.py`      | LAN receiver for device uploads               |
| `usb_import.py`         | USB mass-storage import                       |
| `ble_config` ↔ `device_comm.py` | Bluetooth settings push               |
| `ai_providers.py`       | Local and cloud model calls                   |

Notes and audio are plain files in this folder. Back it up by copying it;
delete a note in the app and its audio goes with it.

## Troubleshooting

**"faster-whisper is not installed"** — expected until you install it. The note
is still saved with its audio; press *Regenerate* once the model is set up.

**Bluetooth push fails** — macOS asks for Bluetooth permission the first time.
Allow it, make sure the device is powered on and advertising (long-press
TRANSMIT to re-advertise), then try *Find device* in Settings.

**Device uploads never arrive** — check the address shown in Settings →
Transfers is on the same network as the device, and that macOS's firewall is not
blocking incoming connections to Python.

**Recording a call captures no audio** — the share picker has a "Share audio"
checkbox that is off by default. The app will tell you if no audio track came
through.
