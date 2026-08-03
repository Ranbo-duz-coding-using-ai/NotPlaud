// SD card mount, recording inventory, and the "already uploaded" manifest.
//
// The card is mounted through esp_vfs_fat_sdmmc_mount rather than the Arduino
// SD_MMC wrapper, because USB mass storage needs the raw sdmmc_card_t handle to
// serve sector reads and writes to the host.
#pragma once

#include <stdint.h>
#include <stddef.h>
#include "sdmmc_cmd.h"

bool storageBegin();
void storageEnd();
bool storageMounted();

// Raw card handle — used by the USB MSC layer.
sdmmc_card_t *storageCard();

uint64_t storageFreeBytes();
uint64_t storageTotalBytes();

// Builds the next recording path, e.g. /sdcard/notplaud/recordings/session_1754200000.wav
// `epoch` is unix seconds when the app has told us the time, otherwise 0 and we
// fall back to a boot-relative name.
void storageNextRecordingPath(char *out, size_t outLen, uint32_t epoch);

// Uploaded-file manifest. Names are bare filenames, one per line.
bool storageWasSent(const char *filename);
void storageMarkSent(const char *filename);

// Iterates recordings that have not been uploaded yet. Call with index 0,1,2…
// until it returns false. Fills `path` and `filename`.
bool storageNextUnsent(int index, char *path, size_t pathLen, char *filename, size_t nameLen);

int storageUnsentCount();
