#include "storage.h"

#include <Arduino.h>
#include <dirent.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/unistd.h>

#include "driver/sdmmc_host.h"
#include "esp_vfs_fat.h"
#include "config.h"

static sdmmc_card_t *card = nullptr;
static bool mounted = false;

sdmmc_card_t *storageCard() { return card; }
bool storageMounted() { return mounted; }

static void ensureDirs() {
  mkdir("/sdcard/notplaud", 0777);
  mkdir(REC_DIR, 0777);

  // Marker file so the desktop app can recognise this card when it appears as
  // a USB volume.
  struct stat st;
  if (stat(ID_FILE, &st) != 0) {
    FILE *f = fopen(ID_FILE, "w");
    if (f) {
      fprintf(f, "notplaud\n");
      fclose(f);
    }
  }
}

bool storageBegin() {
  if (mounted) return true;

  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  host.flags = SDMMC_HOST_FLAG_1BIT;  // 1-bit mode: three pins, ample bandwidth
  host.max_freq_khz = SDMMC_FREQ_DEFAULT;

  sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
  slot.width = 1;
  slot.clk = (gpio_num_t)SD_CLK;
  slot.cmd = (gpio_num_t)SD_CMD;
  slot.d0 = (gpio_num_t)SD_D0;
  slot.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;

  esp_vfs_fat_sdmmc_mount_config_t mountCfg = {
      .format_if_mount_failed = false,
      .max_files = 4,
      .allocation_unit_size = 16 * 1024,
      .disk_status_check_enable = false,
      .use_one_fat = false,
  };

  esp_err_t err = esp_vfs_fat_sdmmc_mount("/sdcard", &host, &slot, &mountCfg, &card);
  if (err != ESP_OK) {
    Serial.printf("[sd] mount failed: %s\n", esp_err_to_name(err));
    mounted = false;
    return false;
  }

  mounted = true;
  ensureDirs();
  Serial.printf("[sd] mounted, %llu MB free\n", storageFreeBytes() / (1024ULL * 1024ULL));
  return true;
}

void storageEnd() {
  if (!mounted) return;
  esp_vfs_fat_sdcard_unmount("/sdcard", card);
  card = nullptr;
  mounted = false;
  Serial.println("[sd] unmounted");
}

uint64_t storageTotalBytes() {
  if (!card) return 0;
  return (uint64_t)card->csd.capacity * card->csd.sector_size;
}

uint64_t storageFreeBytes() {
  if (!mounted) return 0;
  FATFS *fs;
  DWORD freeClusters;
  if (f_getfree("0:", &freeClusters, &fs) != FR_OK) return 0;
  return (uint64_t)freeClusters * fs->csize * 512ULL;
}

void storageNextRecordingPath(char *out, size_t outLen, uint32_t epoch) {
  // The desktop app parses `session_<epoch>` out of the filename to date the
  // note, so use real unix time whenever the app has given it to us.
  if (epoch > 1000000000UL) {
    snprintf(out, outLen, "%s/session_%lu.wav", REC_DIR, (unsigned long)epoch);
  } else {
    snprintf(out, outLen, "%s/session_boot%lu.wav", REC_DIR, (unsigned long)(millis() / 1000));
  }

  // Never silently overwrite an existing recording.
  struct stat st;
  int suffix = 1;
  char candidate[160];
  strncpy(candidate, out, sizeof(candidate) - 1);
  candidate[sizeof(candidate) - 1] = '\0';
  while (stat(candidate, &st) == 0 && suffix < 100) {
    char stem[140];
    strncpy(stem, out, sizeof(stem) - 1);
    stem[sizeof(stem) - 1] = '\0';
    char *dot = strrchr(stem, '.');
    if (dot) *dot = '\0';
    snprintf(candidate, sizeof(candidate), "%s-%d.wav", stem, suffix++);
  }
  strncpy(out, candidate, outLen - 1);
  out[outLen - 1] = '\0';
}

bool storageWasSent(const char *filename) {
  FILE *f = fopen(SENT_FILE, "r");
  if (!f) return false;
  char line[128];
  bool found = false;
  while (fgets(line, sizeof(line), f)) {
    line[strcspn(line, "\r\n")] = '\0';
    if (strcmp(line, filename) == 0) {
      found = true;
      break;
    }
  }
  fclose(f);
  return found;
}

void storageMarkSent(const char *filename) {
  if (storageWasSent(filename)) return;
  FILE *f = fopen(SENT_FILE, "a");
  if (!f) return;
  fprintf(f, "%s\n", filename);
  fclose(f);
}

bool storageNextUnsent(int index, char *path, size_t pathLen, char *filename, size_t nameLen) {
  DIR *dir = opendir(REC_DIR);
  if (!dir) return false;

  int seen = 0;
  struct dirent *entry;
  bool found = false;

  while ((entry = readdir(dir)) != nullptr) {
    if (entry->d_type == DT_DIR) continue;
    const char *name = entry->d_name;
    size_t len = strlen(name);
    if (len < 5 || strcasecmp(name + len - 4, ".wav") != 0) continue;
    if (storageWasSent(name)) continue;

    if (seen == index) {
      snprintf(path, pathLen, "%s/%s", REC_DIR, name);
      strncpy(filename, name, nameLen - 1);
      filename[nameLen - 1] = '\0';
      found = true;
      break;
    }
    seen++;
  }

  closedir(dir);
  return found;
}

int storageUnsentCount() {
  DIR *dir = opendir(REC_DIR);
  if (!dir) return 0;
  int count = 0;
  struct dirent *entry;
  while ((entry = readdir(dir)) != nullptr) {
    if (entry->d_type == DT_DIR) continue;
    const char *name = entry->d_name;
    size_t len = strlen(name);
    if (len < 5 || strcasecmp(name + len - 4, ".wav") != 0) continue;
    if (!storageWasSent(name)) count++;
  }
  closedir(dir);
  return count;
}
