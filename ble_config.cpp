#include "ble_config.h"

#include <Arduino.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <Preferences.h>
#include <string.h>

#include "config.h"

DeviceConfig gConfig;

static BLEServer *server = nullptr;
static BLECharacteristic *statusChar = nullptr;
static Preferences prefs;
static bool connected = false;
static bool pendingApply = false;

bool bleConfigConnected() { return connected; }

void configLoad() {
  prefs.begin("notplaud", true);
  prefs.getString("mode", gConfig.mode, sizeof(gConfig.mode));
  prefs.getString("ssid", gConfig.ssid, sizeof(gConfig.ssid));
  prefs.getString("pass", gConfig.password, sizeof(gConfig.password));
  prefs.getString("name", gConfig.name, sizeof(gConfig.name));
  prefs.getString("host", gConfig.host, sizeof(gConfig.host));
  prefs.getString("token", gConfig.token, sizeof(gConfig.token));
  gConfig.port = prefs.getUShort("port", 8788);
  gConfig.configured = prefs.getBool("cfg", false);
  prefs.end();

  if (gConfig.mode[0] == '\0') strcpy(gConfig.mode, "standard");
  if (gConfig.name[0] == '\0') strcpy(gConfig.name, "NotPlaud Node");
  if (gConfig.port == 0) gConfig.port = 8788;
}

void configSave() {
  prefs.begin("notplaud", false);
  prefs.putString("mode", gConfig.mode);
  prefs.putString("ssid", gConfig.ssid);
  prefs.putString("pass", gConfig.password);
  prefs.putString("name", gConfig.name);
  prefs.putString("host", gConfig.host);
  prefs.putString("token", gConfig.token);
  prefs.putUShort("port", gConfig.port);
  prefs.putBool("cfg", true);
  prefs.end();
  gConfig.configured = true;
}

// Splits `src` on '|' into at most `maxFields` NUL-terminated pieces.
static int splitFields(char *src, char **fields, int maxFields) {
  int count = 0;
  char *cursor = src;
  fields[count++] = cursor;
  while (*cursor && count < maxFields) {
    if (*cursor == '|') {
      *cursor = '\0';
      fields[count++] = cursor + 1;
    }
    cursor++;
  }
  return count;
}

static void copyField(char *dest, size_t destLen, const char *src) {
  if (!src) {
    dest[0] = '\0';
    return;
  }
  strncpy(dest, src, destLen - 1);
  dest[destLen - 1] = '\0';
}

class ConfigCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    std::string value = characteristic->getValue();
    if (value.empty()) return;

    char buffer[320];
    strncpy(buffer, value.c_str(), sizeof(buffer) - 1);
    buffer[sizeof(buffer) - 1] = '\0';

    char *fields[10] = {nullptr};
    int count = splitFields(buffer, fields, 10);

    if (count < 2) {
      bleConfigSetStatus("error:short-payload");
      return;
    }

    int version = atoi(fields[0]);
    if (version != CONFIG_VERSION) {
      Serial.printf("[ble] unsupported config version %d\n", version);
      bleConfigSetStatus("error:version");
      return;
    }

    copyField(gConfig.mode, sizeof(gConfig.mode), count > 1 ? fields[1] : "standard");
    copyField(gConfig.ssid, sizeof(gConfig.ssid), count > 2 ? fields[2] : "");
    copyField(gConfig.password, sizeof(gConfig.password), count > 3 ? fields[3] : "");
    copyField(gConfig.name, sizeof(gConfig.name), count > 4 ? fields[4] : "NotPlaud Node");
    copyField(gConfig.host, sizeof(gConfig.host), count > 5 ? fields[5] : "");
    gConfig.port = (count > 6) ? (uint16_t)atoi(fields[6]) : 8788;
    copyField(gConfig.token, sizeof(gConfig.token), count > 7 ? fields[7] : "");

    if (count > 8) {
      uint32_t epoch = (uint32_t)strtoul(fields[8], nullptr, 10);
      if (epoch > 1000000000UL) {
        gConfig.epochAtSync = epoch;
        gConfig.millisAtSync = millis();
      }
    }

    if (gConfig.port == 0) gConfig.port = 8788;

    configSave();
    pendingApply = true;

    Serial.printf("[ble] config: mode=%s ssid=%s host=%s:%u\n", gConfig.mode, gConfig.ssid,
                  gConfig.host, gConfig.port);
  }
};

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *) override {
    connected = true;
    Serial.println("[ble] app connected");
  }
  void onDisconnect(BLEServer *) override {
    connected = false;
    Serial.println("[ble] app disconnected");
    // Advertise again so the app can reach us next time without a reboot.
    BLEDevice::startAdvertising();
  }
};

void bleConfigBegin() {
  char fullName[64];
  // The app scans for names beginning with "NotPlaud".
  if (strncmp(gConfig.name, BLE_NAME_PREFIX, strlen(BLE_NAME_PREFIX)) == 0) {
    strncpy(fullName, gConfig.name, sizeof(fullName) - 1);
    fullName[sizeof(fullName) - 1] = '\0';
  } else {
    snprintf(fullName, sizeof(fullName), "%s %s", BLE_NAME_PREFIX, gConfig.name);
  }

  BLEDevice::init(fullName);
  server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService *service = server->createService(BLE_SERVICE_UUID);

  BLECharacteristic *configChar = service->createCharacteristic(
      BLE_CHAR_CONFIG_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  configChar->setCallbacks(new ConfigCallbacks());

  statusChar = service->createCharacteristic(
      BLE_CHAR_STATUS_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  statusChar->addDescriptor(new BLE2902());
  statusChar->setValue("idle");

  service->start();

  BLEAdvertising *advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(BLE_SERVICE_UUID);
  advertising->setScanResponse(true);
  BLEDevice::startAdvertising();

  Serial.printf("[ble] advertising as \"%s\"\n", fullName);
}

void bleConfigAdvertise() { BLEDevice::startAdvertising(); }

void bleConfigSetStatus(const char *status) {
  if (!statusChar) return;
  statusChar->setValue((uint8_t *)status, strlen(status));
  if (connected) statusChar->notify();
}

void bleConfigLoop() {
  if (!pendingApply) return;
  pendingApply = false;
  // Capture mode can change mid-session; it takes effect on the next block.
  micArraySetMode(micModeFromName(gConfig.mode));
  bleConfigSetStatus("config-applied");
}
