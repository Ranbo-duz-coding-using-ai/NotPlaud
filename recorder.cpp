#include "recorder.h"

#include <Arduino.h>
#include <stdio.h>
#include <string.h>

#include "config.h"
#include "storage.h"

// Provided by the main sketch: current unix time, or 0 if the app has not
// synced the clock yet.
uint32_t deviceEpochNow();

static FILE *file = nullptr;
static bool active = false;
static bool paused = false;
static uint32_t dataBytes = 0;
static char currentPath[160] = {0};
static char currentName[96] = {0};
static uint32_t flushCounter = 0;

// Standard 44-byte PCM WAV header. Sizes are patched in when we close.
static void writeHeader(FILE *f, uint32_t pcmBytes) {
  const uint32_t sampleRate = SAMPLE_RATE;
  const uint16_t channels = AUDIO_CHANNELS;
  const uint16_t bits = BITS_PER_SAMPLE;
  const uint32_t byteRate = sampleRate * channels * bits / 8;
  const uint16_t blockAlign = channels * bits / 8;

  uint8_t h[44];
  memcpy(h, "RIFF", 4);
  uint32_t riffSize = 36 + pcmBytes;
  memcpy(h + 4, &riffSize, 4);
  memcpy(h + 8, "WAVEfmt ", 8);
  uint32_t fmtSize = 16;
  memcpy(h + 16, &fmtSize, 4);
  uint16_t audioFormat = 1;  // PCM
  memcpy(h + 20, &audioFormat, 2);
  memcpy(h + 22, &channels, 2);
  memcpy(h + 24, &sampleRate, 4);
  memcpy(h + 28, &byteRate, 4);
  memcpy(h + 32, &blockAlign, 2);
  memcpy(h + 34, &bits, 2);
  memcpy(h + 36, "data", 4);
  memcpy(h + 40, &pcmBytes, 4);

  fseek(f, 0, SEEK_SET);
  fwrite(h, 1, sizeof(h), f);
}

bool recorderStart(uint32_t epoch) {
  if (active) return true;
  if (!storageMounted()) {
    Serial.println("[rec] no SD card");
    return false;
  }
  if (storageFreeBytes() < MIN_FREE_BYTES) {
    Serial.println("[rec] card almost full — refusing to record");
    return false;
  }

  storageNextRecordingPath(currentPath, sizeof(currentPath), epoch);
  const char *slash = strrchr(currentPath, '/');
  strncpy(currentName, slash ? slash + 1 : currentPath, sizeof(currentName) - 1);
  currentName[sizeof(currentName) - 1] = '\0';

  file = fopen(currentPath, "wb");
  if (!file) {
    Serial.printf("[rec] could not open %s\n", currentPath);
    return false;
  }

  dataBytes = 0;
  writeHeader(file, 0);  // placeholder, patched on stop
  active = true;
  paused = false;
  flushCounter = 0;
  Serial.printf("[rec] recording -> %s\n", currentPath);
  return true;
}

void recorderStop() {
  if (!active) return;
  active = false;
  paused = false;

  if (file) {
    writeHeader(file, dataBytes);
    fflush(file);
    fclose(file);
    file = nullptr;
  }
  Serial.printf("[rec] saved %s (%lu bytes, %lu s)\n", currentName, (unsigned long)dataBytes,
                (unsigned long)recorderSeconds());
}

void recorderPause() {
  if (!active || paused) return;
  paused = true;
  if (file) fflush(file);
  Serial.println("[rec] paused");
}

void recorderResume() {
  if (!active || !paused) return;
  paused = false;
  Serial.println("[rec] resumed");
}

bool recorderActive() { return active; }
bool recorderPaused() { return paused; }
const char *recorderCurrentFile() { return currentName; }
uint64_t recorderBytes() { return dataBytes; }

uint32_t recorderSeconds() {
  const uint32_t bytesPerSecond = SAMPLE_RATE * AUDIO_CHANNELS * (BITS_PER_SAMPLE / 8);
  return bytesPerSecond ? dataBytes / bytesPerSecond : 0;
}

void recorderWrite(const int16_t *samples, size_t count) {
  if (!active || paused || !file || count == 0) return;

  size_t written = fwrite(samples, sizeof(int16_t), count, file);
  dataBytes += (uint32_t)(written * sizeof(int16_t));

  // Flush roughly once a second, and patch the header while we are at it so a
  // yanked battery still leaves a playable file rather than a 0-length one.
  // The free-space check rides along here too: f_getfree walks the FAT, which
  // is far too slow to run on every 16 ms block.
  if (++flushCounter >= (SAMPLE_RATE / I2S_FRAMES_PER_READ)) {
    flushCounter = 0;
    fflush(file);

    long here = ftell(file);
    writeHeader(file, dataBytes);
    fseek(file, here, SEEK_SET);

    if (storageFreeBytes() < MIN_FREE_BYTES) {
      Serial.println("[rec] card full — stopping");
      recorderStop();
      return;
    }
  }

  if (MAX_FILE_BYTES && dataBytes >= MAX_FILE_BYTES) {
    Serial.println("[rec] size limit reached — rolling to a new file");
    recorderStop();
    recorderStart(deviceEpochNow());
  }
}
