#include "net_upload.h"

#include <Arduino.h>
#include <WiFi.h>
#include <stdio.h>
#include <sys/stat.h>
#include <time.h>

#include "ble_config.h"
#include "config.h"
#include "storage.h"

bool netWifiConnect(uint32_t timeoutMs) {
  if (WiFi.status() == WL_CONNECTED) return true;
  if (gConfig.ssid[0] == '\0') {
    Serial.println("[net] no WiFi network configured");
    return false;
  }

  Serial.printf("[net] joining %s\n", gConfig.ssid);
  WiFi.mode(WIFI_STA);
  WiFi.begin(gConfig.ssid, gConfig.password);

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) {
    delay(200);
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[net] could not join");
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    return false;
  }

  Serial.printf("[net] connected, ip=%s\n", WiFi.localIP().toString().c_str());
  return true;
}

void netWifiDisconnect() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
}

void netSyncTime() {
  if (WiFi.status() != WL_CONNECTED) return;
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  // Short wait — if it does not land quickly we just keep using BLE time.
  for (int i = 0; i < 20; i++) {
    time_t now = time(nullptr);
    if (now > 1000000000L) {
      gConfig.epochAtSync = (uint32_t)now;
      gConfig.millisAtSync = millis();
      Serial.printf("[net] clock synced: %lu\n", (unsigned long)now);
      return;
    }
    delay(100);
  }
}

// Streams one file to POST /upload?name=<file>.
//
// This is a hand-rolled request rather than HTTPClient because the recordings
// live behind a C FILE* on the FAT volume, and HTTPClient only accepts an
// Arduino Stream. Writing the socket directly lets us push the file out in
// small chunks and never hold more than UPLOAD_CHUNK bytes in RAM. The token
// travels as a header so it stays out of the desktop app's request log.
static bool uploadFileRaw(const char *path, const char *filename) {
  struct stat st;
  if (stat(path, &st) != 0) return false;

  if (st.st_size <= 44) {
    Serial.printf("[net] skipping empty %s\n", filename);
    storageMarkSent(filename);
    return true;
  }

  FILE *f = fopen(path, "rb");
  if (!f) return false;

  WiFiClient client;
  client.setTimeout(30000);
  if (!client.connect(gConfig.host, gConfig.port)) {
    Serial.printf("[net] cannot reach %s:%u\n", gConfig.host, gConfig.port);
    fclose(f);
    return false;
  }

  char header[384];
  int headerLen = snprintf(header, sizeof(header),
                           "POST /upload?name=%s HTTP/1.1\r\n"
                           "Host: %s:%u\r\n"
                           "X-NotPlaud-Token: %s\r\n"
                           "Content-Type: audio/wav\r\n"
                           "Content-Length: %lu\r\n"
                           "Connection: close\r\n"
                           "\r\n",
                           filename, gConfig.host, gConfig.port, gConfig.token,
                           (unsigned long)st.st_size);
  client.write((const uint8_t *)header, headerLen);

  static uint8_t chunk[UPLOAD_CHUNK];
  size_t remaining = st.st_size;
  while (remaining > 0 && client.connected()) {
    size_t want = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
    size_t got = fread(chunk, 1, want, f);
    if (got == 0) break;
    size_t written = client.write(chunk, got);
    if (written != got) break;
    remaining -= got;
    // Keep the watchdog happy on long transfers.
    yield();
  }
  fclose(f);

  if (remaining != 0) {
    Serial.printf("[net] transfer of %s was cut short\n", filename);
    client.stop();
    return false;
  }

  // Read just the status line — that is all we need to know.
  uint32_t deadline = millis() + 15000;
  String statusLine;
  while (millis() < deadline) {
    if (client.available()) {
      statusLine = client.readStringUntil('\n');
      break;
    }
    delay(10);
  }
  client.stop();

  bool ok = statusLine.indexOf("200") > 0;
  Serial.printf("[net] %s -> %s\n", filename, ok ? "ok" : statusLine.c_str());
  return ok;
}

UploadResult netUploadAll(void (*onProgress)(int, int, const char *)) {
  UploadResult result = {false, 0, 0, ""};

  if (gConfig.host[0] == '\0') {
    snprintf(result.message, sizeof(result.message), "No upload address — open the app and push settings");
    return result;
  }

  int total = storageUnsentCount();
  if (total == 0) {
    result.wifiOk = true;
    snprintf(result.message, sizeof(result.message), "Nothing new to send");
    return result;
  }

  if (!netWifiConnect(WIFI_TIMEOUT_MS)) {
    snprintf(result.message, sizeof(result.message), "Could not join %s", gConfig.ssid);
    return result;
  }
  result.wifiOk = true;
  netSyncTime();

  // Always re-read index 0: successfully sent files leave the unsent list, so
  // the next pending one shifts down into that slot.
  char path[160];
  char name[96];
  int guard = 0;
  int index = 0;

  while (guard++ < 512 && storageNextUnsent(index, path, sizeof(path), name, sizeof(name))) {
    if (onProgress) onProgress(result.sent + result.failed + 1, total, name);

    if (uploadFileRaw(path, name)) {
      storageMarkSent(name);
      result.sent++;
      index = 0;  // list shrank; restart from the top
    } else {
      result.failed++;
      index++;  // leave it for next time and move past it
    }
  }

  netWifiDisconnect();
  snprintf(result.message, sizeof(result.message), "Sent %d, failed %d", result.sent, result.failed);
  Serial.printf("[net] %s\n", result.message);
  return result;
}
