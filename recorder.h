// Streams mixed mono audio into a WAV file on the SD card.
#pragma once

#include <stdint.h>
#include <stddef.h>

bool recorderStart(uint32_t epoch);
void recorderStop();

void recorderPause();
void recorderResume();

bool recorderActive();   // started and not stopped
bool recorderPaused();

// Feed one block of samples. No-op while paused.
void recorderWrite(const int16_t *samples, size_t count);

uint32_t recorderSeconds();
uint64_t recorderBytes();
const char *recorderCurrentFile();
