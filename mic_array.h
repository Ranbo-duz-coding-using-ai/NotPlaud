// Four-microphone I2S capture with per-mode processing.
#pragma once

#include <stdint.h>
#include <stddef.h>

enum CaptureMode {
  MODE_STANDARD = 0,
  MODE_WIDE_SPECTRUM = 1,
  MODE_VOICE_ISOLATION = 2,
};

// Maps the mode strings the desktop app sends ("standard", "wide-spectrum",
// "voice-isolation") onto the enum above.
CaptureMode micModeFromName(const char *name);
const char *micModeName(CaptureMode mode);

// Brings up both I2S buses. Returns false if either channel fails to start.
bool micArrayBegin();
void micArrayEnd();

void micArraySetMode(CaptureMode mode);
CaptureMode micArrayGetMode();

// Reads one block from both buses and mixes the four microphones down to mono
// according to the active mode. Writes at most `maxSamples` int16 samples into
// `out` and returns how many it produced (0 on a read timeout).
size_t micArrayRead(int16_t *out, size_t maxSamples);

// Peak level of the most recent block, 0.0–1.0. Drives the status LED.
float micArrayLevel();
