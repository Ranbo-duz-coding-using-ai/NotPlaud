// WiFi batch upload of everything the device has not sent yet.
#pragma once

#include <stdint.h>

struct UploadResult {
  bool wifiOk;
  int sent;
  int failed;
  char message[96];
};

// Connects to the configured network, uploads every unsent recording, then
// drops WiFi again to save battery. Blocking — the caller stops recording
// first. Progress is reported through the callback if supplied.
UploadResult netUploadAll(void (*onProgress)(int index, int total, const char *name));

bool netWifiConnect(uint32_t timeoutMs);
void netWifiDisconnect();

// Pulls the current time over SNTP once WiFi is up, so recordings get real
// timestamps even if the app has not pushed one over BLE.
void netSyncTime();
