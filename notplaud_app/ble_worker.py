#!/usr/bin/env python3
"""Bluetooth work, isolated in its own process.

Why a separate process: on macOS, CoreBluetooth (which bleak sits on) will
*terminate* the host process outright if the app has not been granted Bluetooth
permission, or when it is driven from the wrong thread. That is a hard kill —
no Python exception is raised and no `try`/`except` can catch it. Running BLE
here means the worst case is a dead subprocess and an error message, instead of
the whole NotPlaud app vanishing mid-click.

Prints exactly one JSON object on stdout. Never import this from the app; run it.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys

NOTPLAUD_CHAR_CONFIG_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTPLAUD_CHAR_STATUS_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME_PREFIX = "NotPlaud"


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


async def find_address(timeout: float) -> str | None:
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=timeout)
    for device in devices:
        if (device.name or "").startswith(DEVICE_NAME_PREFIX):
            return device.address
    return None


async def do_push(payload: bytes, address: str | None, timeout: float) -> dict:
    from bleak import BleakClient

    if not address:
        address = await find_address(min(timeout, 8.0))
        if not address:
            return {
                "ok": False,
                "error": "No NotPlaud device found. Power it on, and long-press TRANSMIT to re-advertise.",
            }

    async with BleakClient(address, timeout=timeout) as client:
        await client.write_gatt_char(NOTPLAUD_CHAR_CONFIG_UUID, payload, response=True)
        status = ""
        try:
            raw = await client.read_gatt_char(NOTPLAUD_CHAR_STATUS_UUID)
            status = bytes(raw).decode("utf-8", "replace")
        except Exception:
            pass
        return {"ok": True, "address": address, "bytes": len(payload), "device": status}


async def do_scan(timeout: float) -> dict:
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=timeout)
    found = [
        {"name": device.name or "", "address": device.address}
        for device in devices
        if DEVICE_NAME_PREFIX in (device.name or "")
    ]
    return {"ok": True, "devices": found}


def main() -> None:
    parser = argparse.ArgumentParser(description="NotPlaud BLE worker")
    parser.add_argument("command", choices=["push", "scan"])
    parser.add_argument("--payload", default="", help="base64-encoded config payload")
    parser.add_argument("--address", default="")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()

    try:
        import bleak  # noqa: F401
    except ImportError:
        emit({"ok": False, "error": "bleak is not installed. Run: pip install bleak"})
        return

    try:
        if args.command == "push":
            payload = base64.b64decode(args.payload) if args.payload else b""
            result = asyncio.run(do_push(payload, args.address or None, args.timeout))
        else:
            result = asyncio.run(do_scan(args.timeout))
        emit(result)
    except Exception as exc:
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
