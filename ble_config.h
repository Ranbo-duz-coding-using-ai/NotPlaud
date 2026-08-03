// BLE configuration service.
//
// The desktop app writes one pipe-delimited string to the config
// characteristic (see notplaud_app/device_comm.py):
//
//   2|mode|ssid|password|name|host|port|token|epoch
//
// Everything is persisted to NVS so the device keeps working after a power
// cycle without needing the app again.
#pragma once

#include <stdint.h>
#include "mic_array.h"

struct DeviceConfig {
  char mode[24];
  char ssid[64];
  char password[64];
  char name[40];
  char host[48];
  uint16_t port;
  char token[40];
  uint32_t epochAtSync;   // unix time the app reported
  uint32_t millisAtSync;  // millis() when we received it
  bool configured;
};

extern DeviceConfig gConfig;

void bleConfigBegin();
void bleConfigLoop();

// Publishes a short human-readable status string the app can read back.
void bleConfigSetStatus(const char *status);

void bleConfigAdvertise();
bool bleConfigConnected();

// Persisted config helpers.
void configLoad();
void configSave();
