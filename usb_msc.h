// USB mass-storage handover.
//
// Plug the device into a computer with a data cable and the SD card shows up as
// an ordinary removable drive, so the desktop app can copy recordings straight
// off it. While the host owns the card the firmware must not touch the
// filesystem, so recording stops and FATFS is unmounted first.
#pragma once

#include <stdint.h>

void usbMscBegin();
void usbMscLoop();

// True while the host has the card mounted.
bool usbMscActive();
