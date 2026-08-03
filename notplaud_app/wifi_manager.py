"""WiFi network detection and management for NotPlaud."""

from __future__ import annotations

import platform
import subprocess
import uuid
from typing import Any


def detect_current_ssid() -> str:
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["/usr/sbin/networksetup", "-getairportnetwork", "en0"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            line = result.stdout.strip()
            if "Current Wi-Fi Network:" in line:
                return line.split(":", 1)[1].strip()
            if "You are not associated" in line:
                return ""
        elif system == "Linux":
            result = subprocess.run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for row in result.stdout.splitlines():
                active, _, ssid = row.partition(":")
                if active == "yes" and ssid:
                    return ssid
        elif system == "Windows":
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        return parts[1].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def match_saved_network(ssid: str, networks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not ssid:
        return None
    matches = [item for item in networks if item.get("ssid") == ssid]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.get("priority", 0), reverse=True)[0]


def normalize_network(payload: dict[str, Any]) -> dict[str, Any]:
    ssid = str(payload.get("ssid", "")).strip()
    if not ssid:
        raise ValueError("SSID is required.")
    return {
        "id": payload.get("id") or uuid.uuid4().hex[:12],
        "ssid": ssid[:64],
        "password": str(payload.get("password", "")),
        "priority": int(payload.get("priority", 0) or 0),
        "auto_connect": bool(payload.get("auto_connect", True)),
    }
