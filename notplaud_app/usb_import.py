"""Import recordings from the device's SD card when it is plugged in over USB.

The firmware exposes the SD card as a USB mass-storage volume the moment a data
cable is connected, so the card simply shows up as a normal removable drive. We
look for a volume that carries a ``notplaud`` marker and copy anything we have
not already imported.
"""

from __future__ import annotations

import platform
import shutil
import string
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac", ".aac"}

# Directories the firmware writes to on the card, in priority order.
RECORDING_SUBDIRS = ("notplaud/recordings", "NOTPLAUD/RECORDINGS", "recordings", "")

# A volume counts as a NotPlaud card if any of these exist at its root.
MARKERS = ("notplaud.id", "NOTPLAUD.ID", "notplaud", "NOTPLAUD")


def candidate_volumes() -> list[Path]:
    system = platform.system()
    roots: list[Path] = []
    if system == "Darwin":
        roots = [p for p in Path("/Volumes").glob("*") if p.is_dir()]
    elif system == "Linux":
        for base in (Path("/media"), Path("/run/media"), Path("/mnt")):
            if not base.exists():
                continue
            for entry in base.glob("*"):
                if entry.is_dir():
                    roots.append(entry)
                    roots.extend(child for child in entry.glob("*") if child.is_dir())
    elif system == "Windows":
        roots = [Path(f"{letter}:/") for letter in string.ascii_uppercase if Path(f"{letter}:/").exists()]
    return roots


def is_notplaud_volume(volume: Path, hint: str = "NOTPLAUD") -> bool:
    try:
        if hint and hint.lower() in volume.name.lower():
            return True
        for marker in MARKERS:
            if (volume / marker).exists():
                return True
    except OSError:
        return False
    return False


def find_device_volumes(hint: str = "NOTPLAUD") -> list[Path]:
    return [volume for volume in candidate_volumes() if is_notplaud_volume(volume, hint)]


def recordings_dir(volume: Path) -> Path:
    for sub in RECORDING_SUBDIRS:
        candidate = volume / sub if sub else volume
        if candidate.is_dir() and any(
            child.suffix.lower() in AUDIO_EXTENSIONS for child in candidate.iterdir() if child.is_file()
        ):
            return candidate
    return volume


def scan(hint: str = "NOTPLAUD") -> list[dict]:
    """Report pluggable volumes and how many new recordings each holds."""
    results = []
    for volume in find_device_volumes(hint):
        source = recordings_dir(volume)
        try:
            files = [
                child
                for child in source.iterdir()
                if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS
            ]
        except OSError:
            files = []
        results.append(
            {
                "volume": str(volume),
                "name": volume.name,
                "source": str(source),
                "files": len(files),
                "bytes": sum(child.stat().st_size for child in files),
            }
        )
    return results


def import_from_volume(volume: Path, incoming_dir: Path, seen_names: set[str]) -> list[str]:
    """Copy not-yet-imported audio into the incoming folder. Never deletes."""
    source = recordings_dir(volume)
    copied: list[str] = []
    incoming_dir.mkdir(parents=True, exist_ok=True)

    try:
        entries = sorted(source.iterdir())
    except OSError:
        return copied

    for child in entries:
        if not child.is_file() or child.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if child.name in seen_names:
            continue
        target = incoming_dir / child.name
        counter = 1
        while target.exists():
            target = incoming_dir / f"{child.stem}-{counter}{child.suffix}"
            counter += 1
        part = target.with_suffix(target.suffix + ".part")
        try:
            shutil.copy2(child, part)
            part.replace(target)
        except OSError:
            part.unlink(missing_ok=True)
            continue
        copied.append(target.name)
    return copied


def import_all(incoming_dir: Path, seen_names: set[str], hint: str = "NOTPLAUD") -> dict:
    volumes = find_device_volumes(hint)
    if not volumes:
        return {"ok": False, "error": "No NotPlaud USB volume found.", "copied": []}
    copied: list[str] = []
    for volume in volumes:
        copied.extend(import_from_volume(volume, incoming_dir, seen_names | set(copied)))
    return {"ok": True, "copied": copied, "volumes": [str(v) for v in volumes]}
