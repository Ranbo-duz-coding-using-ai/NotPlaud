"""Bluetooth LE communication with the NotPlaud ESP32-S3 device.

The app is the only thing that ever configures the device. Over a single BLE
characteristic write we hand it:

  * which microphone capture mode to use
  * which WiFi network to join (SSID + password)
  * where to upload finished recordings (host, port, shared token)
  * the current time, since the device has no battery-backed clock

The payload is pipe-delimited rather than JSON to stay comfortably inside a
single BLE write on the firmware side.

All actual radio work happens in `ble_worker.py`, in a separate process. On
macOS, CoreBluetooth terminates the calling process outright when Bluetooth
permission has not been granted — that is a hard kill no exception handler can
intercept, so it must not happen inside the app.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Nordic UART Service-style UUIDs for NotPlaud config
NOTPLAUD_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NOTPLAUD_CHAR_CONFIG_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTPLAUD_CHAR_STATUS_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

DEVICE_NAME_PREFIX = "NotPlaud"

# Bump when the wire format changes so old firmware can reject cleanly.
CONFIG_VERSION = "2"

WORKER = Path(__file__).resolve().parent / "ble_worker.py"

BLUETOOTH_PERMISSION_HINT = (
    "Bluetooth is unavailable. On macOS, grant Bluetooth access to your terminal "
    "or to Python under System Settings > Privacy & Security > Bluetooth, then try again."
)

_last_status: dict[str, Any] = {"connected": False, "message": "Not connected"}
_lock = threading.Lock()


def get_device_status() -> dict[str, Any]:
    with _lock:
        return dict(_last_status)


def _set_status(**kwargs: Any) -> None:
    with _lock:
        _last_status.update(kwargs)


def _clean(value: str) -> str:
    """Pipes and newlines would break the delimiter, so strip them."""
    return str(value or "").replace("|", " ").replace("\n", " ").replace("\r", " ")


def build_config_payload(
    *,
    device_mode: str,
    wifi_ssid: str = "",
    wifi_password: str = "",
    device_name: str = "NotPlaud",
    host: str = "",
    port: int = 8788,
    token: str = "",
    epoch: int | None = None,
) -> bytes:
    """v2|mode|ssid|password|name|host|port|token|epoch

    The device has no battery-backed clock, so we hand it the current unix time
    on every push. That is what lets recordings be named session_<epoch>.wav and
    show the right date in the app.
    """
    parts = [
        CONFIG_VERSION,
        _clean(device_mode) or "standard",
        _clean(wifi_ssid),
        _clean(wifi_password),
        _clean(device_name) or "NotPlaud",
        _clean(host),
        str(int(port or 8788)),
        _clean(token),
        str(int(time.time()) if epoch is None else int(epoch)),
    ]
    return "|".join(parts).encode("utf-8")


def _run_worker(args: list[str], timeout: float) -> dict[str, Any]:
    """Run ble_worker.py and parse its single JSON line."""
    if not WORKER.exists():
        return {"ok": False, "error": "ble_worker.py is missing from the app folder."}

    try:
        completed = subprocess.run(
            [sys.executable, str(WORKER), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Bluetooth timed out. Is the device powered on and nearby?"}
    except OSError as exc:
        return {"ok": False, "error": f"Could not start the Bluetooth helper: {exc}"}

    output = (completed.stdout or "").strip()
    if output:
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            pass

    # No parseable output means the helper was killed rather than returning —
    # on macOS that is almost always the Bluetooth permission prompt being
    # denied or never shown.
    stderr = (completed.stderr or "").strip()
    if completed.returncode and completed.returncode < 0:
        return {"ok": False, "error": BLUETOOTH_PERMISSION_HINT}
    return {"ok": False, "error": stderr or BLUETOOTH_PERMISSION_HINT}


def push_config_sync(
    *,
    device_mode: str,
    wifi_ssid: str = "",
    wifi_password: str = "",
    device_name: str = "NotPlaud",
    host: str = "",
    port: int = 8788,
    token: str = "",
    epoch: int | None = None,
    device_address: str | None = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Push capture mode, WiFi credentials, upload target, and clock over BLE."""
    payload = build_config_payload(
        device_mode=device_mode,
        wifi_ssid=wifi_ssid,
        wifi_password=wifi_password,
        device_name=device_name,
        host=host,
        port=port,
        token=token,
        epoch=epoch,
    )

    args = ["push", "--payload", base64.b64encode(payload).decode(), "--timeout", str(timeout)]
    if device_address:
        args += ["--address", device_address]

    # Give the subprocess headroom over its own internal timeout.
    result = _run_worker(args, timeout=timeout + 15.0)

    if result.get("ok"):
        _set_status(
            connected=True,
            message=f"Config pushed to {result.get('address', 'device')}",
            address=result.get("address", ""),
            device=result.get("device", ""),
        )
    else:
        _set_status(connected=False, message=result.get("error", "Bluetooth push failed"))
    return result


def scan_devices_sync(timeout: float = 6.0) -> list[dict[str, str]]:
    result = _run_worker(["scan", "--timeout", str(timeout)], timeout=timeout + 15.0)
    if result.get("ok"):
        return result.get("devices", [])
    return [{"error": result.get("error", "Bluetooth scan failed")}]
