#!/usr/bin/env python3
"""NotPlaud — local desktop app server for DIY audio notes."""

from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import json
import mimetypes
import re
import secrets
import shutil
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import ingest_server
import jobs
import usb_import
from ai_providers import normalize_detail, summarize_transcript, transcribe_audio
from device_comm import get_device_status, push_config_sync, scan_devices_sync
from wifi_manager import detect_current_ssid, match_saved_network, normalize_network


APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"
DATA_DIR = APP_ROOT / "data"
INCOMING_DIR = DATA_DIR / "incoming"
AUDIO_DIR = DATA_DIR / "audio"
STATE_FILE = DATA_DIR / "app_state.json"

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac", ".aac"}
STATE_LOCK = threading.RLock()
SERVER: ThreadingHTTPServer | None = None
FILE_PICKER = None

DEVICE_MODES = [
    {
        "id": "standard",
        "name": "Standard",
        "description": "Balanced capture for desks, small rooms, and everyday voice notes.",
    },
    {
        "id": "wide-spectrum",
        "name": "Wide Spectrum",
        "description": "Keeps more room ambience and multiple speakers for group discussions.",
    },
    {
        "id": "voice-isolation",
        "name": "Voice Isolation",
        "description": "Prioritizes speech clarity when the room is noisy or the speaker is farther away.",
    },
]

PRESETS = [
    {
        "id": "conference",
        "name": "Conference",
        "description": "Decisions, tradeoffs, owners, next steps, blockers, and follow-ups.",
    },
    {
        "id": "online-class",
        "name": "Online Class",
        "description": "Core concepts, examples, homework, exam hints, and questions to revisit.",
    },
    {
        "id": "interview",
        "name": "Interview",
        "description": "Questions, answers, themes, quotes, and candidate or guest signals.",
    },
    {
        "id": "voice-memo",
        "name": "Voice Memo",
        "description": "Ideas, tasks, reminders, and cleaned-up personal notes.",
    },
    {
        "id": "general",
        "name": "General Notes",
        "description": "A tidy summary with key points, action items, and open questions.",
    },
]

# Prompt-side instructions for each detail target.
DETAIL_GUIDANCE = {
    "low": "Minimal bullets only. Strip filler and keep the highest-signal takeaways.",
    "medium": "Balanced note with enough detail to be useful later without becoming a transcript.",
    "high": "Detailed note with examples, context, reasoning, and grouped subtopics.",
    "ultra": "Maximum detail. Preserve nuance, quotes, timelines, and secondary threads when present.",
}

# UI-side cards for the detail selector (same styling as the device mode cards).
DETAIL_LEVEL_CARDS = [
    {
        "id": "low",
        "name": "Low",
        "description": "A quick skim. Just the handful of things worth remembering.",
    },
    {
        "id": "medium",
        "name": "Medium",
        "description": "The everyday default. Enough context to be useful weeks later.",
    },
    {
        "id": "high",
        "name": "High",
        "description": "Thorough notes with examples, reasoning, and grouped subtopics.",
    },
    {
        "id": "ultra",
        "name": "Ultra",
        "description": "Keeps nuance, quotes, and side threads. Best for lectures and long meetings.",
    },
]

DETAIL_ALIASES = {"short": "low", "normal": "medium", "long": "high"}

DEFAULT_SETTINGS = {
    "device_name": "NotPlaud Node",
    "device_mode": "standard",
    "device_preset": "conference",
    "computer_source": "system-and-mic",
    "computer_preset": "conference",
    "default_detail": "medium",
    "auto_process": True,
    "theme": "dark",
    "summary_source": "local",
    "summary_api_provider": "openai",
    "summary_api_model": "gpt-4o-mini",
    "summary_api_key": "",
    "summary_api_key_env": "OPENAI_API_KEY",
    "local_summary_model_path": "",
    "transcription_source": "local",
    "transcription_api_provider": "openai",
    "transcription_api_model": "whisper-1",
    "transcription_api_key": "",
    "transcription_api_key_env": "OPENAI_API_KEY",
    "local_transcription_model_path": "base",
    "wifi_networks": [],
    "auto_wifi_sync": True,
    "ble_device_address": "",
    "incoming_path": str(INCOMING_DIR),
    # LAN receiver the device uploads to over WiFi
    "ingest_enabled": True,
    "ingest_port": 8788,
    "ingest_token": "",
    # USB mass-storage handover
    "usb_auto_import": True,
    "usb_volume_hint": "NOTPLAUD",
    # Legacy keys kept for migration
    "openai_api_key": "",
    "api_key_env": "OPENAI_API_KEY",
    "chat_model": "gpt-4o-mini",
    "transcription_model": "whisper-1",
    "ai_provider": "local",
}

DEFAULT_FOLDERS = [
    {"id": "inbox", "name": "Inbox", "created_at": "system"},
    {"id": "classes", "name": "Classes", "created_at": "system"},
    {"id": "meetings", "name": "Meetings", "created_at": "system"},
]

SECRET_SETTINGS_KEYS = {"summary_api_key", "transcription_api_key", "openai_api_key"}

# Fields the worker thread is allowed to write back onto a note. Anything the
# user can change while processing runs (folder, title) is deliberately absent
# unless the model produced it.
JOB_RESULT_FIELDS = (
    "status",
    "transcript",
    "raw_transcript",
    "summary",
    "summaries",
    "tags",
    "action_items",
    "detail_level",
    "error",
    "updated_at",
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def migrate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_SETTINGS | settings
    if merged.get("default_detail") in DETAIL_ALIASES:
        merged["default_detail"] = DETAIL_ALIASES[merged["default_detail"]]
    if merged.get("default_detail") not in DETAIL_GUIDANCE:
        merged["default_detail"] = "medium"
    if merged.get("theme") not in {"light", "dark"}:
        merged["theme"] = "dark"
    if not merged.get("local_transcription_model_path"):
        merged["local_transcription_model_path"] = merged.get("transcription_model") or "base"
    if not merged.get("summary_api_model"):
        merged["summary_api_model"] = merged.get("chat_model") or "gpt-4o-mini"
    if not merged.get("transcription_api_model"):
        merged["transcription_api_model"] = merged.get("transcription_model") or "whisper-1"
    if not merged.get("ingest_token"):
        merged["ingest_token"] = secrets.token_hex(8)
    try:
        merged["ingest_port"] = int(merged.get("ingest_port") or 8788)
    except (TypeError, ValueError):
        merged["ingest_port"] = 8788
    legacy_key = merged.get("openai_api_key", "")
    if legacy_key and legacy_key != "********":
        if not merged.get("summary_api_key"):
            merged["summary_api_key"] = legacy_key
        if not merged.get("transcription_api_key"):
            merged["transcription_api_key"] = legacy_key
    merged["incoming_path"] = str(INCOMING_DIR)
    merged.setdefault("wifi_networks", [])
    return merged


def load_state() -> dict[str, Any]:
    ensure_directories()
    if not STATE_FILE.exists():
        state = {
            "notes": [],
            "folders": DEFAULT_FOLDERS,
            "settings": migrate_settings({}),
        }
        save_state(state)
        return state

    with STATE_FILE.open("r", encoding="utf-8") as handle:
        state = json.load(handle)

    state.setdefault("notes", [])
    state.setdefault("folders", DEFAULT_FOLDERS)
    stored = state.get("settings", {})
    state["settings"] = migrate_settings(stored)
    # Migration can mint values that must stay stable across loads (most
    # importantly the ingest token, which the device authenticates with), so
    # anything new gets written straight back to disk.
    if state["settings"] != stored:
        save_state(state)
    return state


def save_state(state: dict[str, Any]) -> None:
    ensure_directories()
    tmp_file = STATE_FILE.with_suffix(".tmp")
    with tmp_file.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
    tmp_file.replace(STATE_FILE)


def mask_settings(settings: dict[str, Any]) -> dict[str, Any]:
    # Deep copy: a shallow copy would let the masking below overwrite the real
    # WiFi passwords held in the live state dict.
    safe = copy.deepcopy(settings)
    for key in SECRET_SETTINGS_KEYS:
        if safe.get(key):
            safe[key] = "********"
    for network in safe.get("wifi_networks", []):
        if network.get("password"):
            network["password"] = "********"
    return safe


# --- Cheap caches -----------------------------------------------------------
# bootstrap is polled every few seconds; SSID lookup and volume scans shell out,
# so they get short TTLs instead of running on every poll.

_SSID_CACHE: dict[str, Any] = {"value": "", "at": 0.0}
_USB_CACHE: dict[str, Any] = {"value": [], "at": 0.0}


def cached_ssid(ttl: float = 15.0) -> str:
    now = time.monotonic()
    if now - _SSID_CACHE["at"] > ttl:
        _SSID_CACHE["value"] = detect_current_ssid()
        _SSID_CACHE["at"] = now
    return _SSID_CACHE["value"]


def cached_usb(hint: str, ttl: float = 8.0) -> list[dict[str, Any]]:
    now = time.monotonic()
    if now - _USB_CACHE["at"] > ttl:
        try:
            _USB_CACHE["value"] = usb_import.scan(hint)
        except Exception:
            _USB_CACHE["value"] = []
        _USB_CACHE["at"] = now
    return _USB_CACHE["value"]


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    settings = state["settings"]
    return {
        "notes": sorted(state["notes"], key=lambda item: item.get("created_at", ""), reverse=True),
        "folders": state["folders"],
        "settings": mask_settings(settings),
        "deviceModes": DEVICE_MODES,
        "presets": PRESETS,
        "detailLevels": DETAIL_LEVEL_CARDS,
        "detailGuidance": DETAIL_GUIDANCE,
        "deviceStatus": get_device_status(),
        "currentWifi": cached_ssid(),
        "processing": jobs.pending_ids(),
        "ingest": {
            "running": ingest_server.is_running(),
            "host": ingest_server.local_ip(),
            "port": settings.get("ingest_port", 8788),
            "token": settings.get("ingest_token", ""),
        },
        "usbVolumes": cached_usb(settings.get("usb_volume_hint", "NOTPLAUD")),
    }


def normalize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return cleaned or "audio"


def parse_session_time(filename: str, fallback: float) -> str:
    match = re.search(r"session_(\d{10,})", filename)
    if match:
        return dt.datetime.fromtimestamp(int(match.group(1)), dt.timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
    return dt.datetime.fromtimestamp(fallback, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mime_for_path(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def find_note(state: dict[str, Any], note_id: str) -> dict[str, Any] | None:
    return next((note for note in state["notes"] if note.get("id") == note_id), None)


def preset_by_id(preset_id: str) -> dict[str, str]:
    return next((preset for preset in PRESETS if preset["id"] == preset_id), PRESETS[-1])


def mode_by_id(mode_id: str) -> dict[str, str]:
    return next((mode for mode in DEVICE_MODES if mode["id"] == mode_id), DEVICE_MODES[0])


def add_note_for_audio(
    state: dict[str, Any],
    audio_path: Path,
    *,
    original_name: str,
    source: str,
    preset_id: str,
    capture_mode_id: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    note_id = uuid.uuid4().hex
    extension = audio_path.suffix.lower() or ".wav"
    final_name = f"{note_id}{extension}"
    final_path = AUDIO_DIR / final_name
    shutil.move(str(audio_path), final_path)
    stat = final_path.stat()

    note = {
        "id": note_id,
        "title": title_from_filename(original_name),
        "created_at": created_at or utc_now(),
        "updated_at": utc_now(),
        "source": source,
        "folder_id": "inbox",
        "preset_id": preset_id,
        "capture_mode_id": capture_mode_id,
        "detail_level": normalize_detail(state["settings"].get("default_detail", "medium")),
        "audio_filename": final_name,
        "original_filename": original_name,
        "mime_type": mime_for_path(final_path),
        "size_bytes": stat.st_size,
        "sha256": file_sha256(final_path),
        "status": "queued",
        "transcript": "",
        "raw_transcript": "",
        "summary": "",
        "summaries": {},
        "tags": [],
        "action_items": [],
        "error": "",
    }
    state["notes"].append(note)
    return note


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"session[_-]?", "Session ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[_-]+", " ", stem).strip()
    return stem.title() if stem else "Untitled Recording"


def scan_incoming(state: dict[str, Any], min_age: float = 2.0) -> list[dict[str, Any]]:
    """Import audio sitting in the incoming folder.

    ``min_age`` guards against grabbing a file that some other process is still
    writing. Uploads that arrive through the ingest server are already complete
    (they are renamed into place atomically), so those pass min_age=0.
    """
    imported: list[dict[str, Any]] = []
    now = dt.datetime.now().timestamp()
    settings = state["settings"]

    for path in sorted(INCOMING_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if min_age and now - path.stat().st_mtime < min_age:
            continue

        created_at = parse_session_time(path.name, path.stat().st_mtime)
        note = add_note_for_audio(
            state,
            path,
            original_name=path.name,
            source="device",
            preset_id=settings.get("device_preset", "conference"),
            capture_mode_id=settings.get("device_mode", "standard"),
            created_at=created_at,
        )
        imported.append(note)

    return imported


def create_note_from_upload(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    data_url = payload.get("data", "")
    filename = normalize_filename(payload.get("filename", "computer-recording.webm"))
    if "," in data_url and data_url.startswith("data:"):
        data_url = data_url.split(",", 1)[1]

    audio_bytes = base64.b64decode(data_url)
    extension = Path(filename).suffix.lower() or ".webm"
    tmp_name = f"upload-{uuid.uuid4().hex}{extension}"
    tmp_path = INCOMING_DIR / tmp_name
    tmp_path.write_bytes(audio_bytes)

    return add_note_for_audio(
        state,
        tmp_path,
        original_name=filename,
        source=payload.get("source", "computer"),
        preset_id=payload.get("preset_id") or state["settings"].get("computer_preset", "conference"),
        capture_mode_id=payload.get("capture_mode_id") or payload.get("computer_source", "system-and-mic"),
    )


# --- Background processing --------------------------------------------------


def queue_note(note_id: str, detail_level: str | None = None) -> None:
    """Mark a note as queued and hand it to the worker thread."""
    with STATE_LOCK:
        state = load_state()
        note = find_note(state, note_id)
        if note:
            note["status"] = "queued"
            note["error"] = ""
            note["updated_at"] = utc_now()
            save_state(state)
    jobs.enqueue(note_id, detail_level)


def run_note_job(note_id: str, detail_level: str | None = None) -> None:
    """Worker entry point. Heavy model work happens outside the state lock."""
    with STATE_LOCK:
        state = load_state()
        note = find_note(state, note_id)
        if not note:
            return
        note["status"] = "processing"
        note["updated_at"] = utc_now()
        save_state(state)
        working = jobs.snapshot(note)
        settings = jobs.snapshot(state["settings"])

    process_note(working, settings, detail_level)

    with STATE_LOCK:
        state = load_state()
        note = find_note(state, note_id)
        if not note:
            return
        for field in JOB_RESULT_FIELDS:
            if field in working:
                note[field] = working[field]
        # Only adopt a model-generated title if the user has not renamed it.
        if working.get("title") and note.get("title") == working.get("_original_title", note.get("title")):
            note["title"] = working["title"]
        save_state(state)


def process_note(note: dict[str, Any], settings: dict[str, Any], detail_level: str | None = None) -> None:
    detail = normalize_detail(detail_level or note.get("detail_level") or settings.get("default_detail", "medium"))
    audio_path = AUDIO_DIR / note["audio_filename"]
    note["_original_title"] = note.get("title")
    note["status"] = "processing"
    note["error"] = ""
    note["updated_at"] = utc_now()

    transcript = note.get("transcript", "")
    if not transcript or transcript.startswith("[Transcript pending"):
        transcript, raw_transcript, transcript_error = transcribe_audio(audio_path, settings)
        if transcript_error:
            note["status"] = "needs_ai"
            note["error"] = transcript_error
            note["transcript"] = "[Transcript pending: configure a local or API transcription model in Settings.]"
            note["raw_transcript"] = transcript_error
            note["summary"] = fallback_summary(note, detail)
            note.setdefault("summaries", {})[detail] = note["summary"]
            note["updated_at"] = utc_now()
            return
        note["transcript"] = transcript
        note["raw_transcript"] = raw_transcript

    summary_result, summary_error = summarize_transcript(note, settings, detail)
    if summary_error:
        note["status"] = "needs_ai"
        note["error"] = summary_error
        note["summary"] = fallback_summary(note, detail)
        note.setdefault("summaries", {})[detail] = note["summary"]
        note["updated_at"] = utc_now()
        return

    if summary_result.get("title"):
        note["title"] = summary_result["title"].strip()[:120]
    note["summary"] = summary_result.get("summary_markdown", "").strip() or fallback_summary(note, detail)
    note.setdefault("summaries", {})[detail] = note["summary"]
    note["tags"] = summary_result.get("tags", [])[:8] if isinstance(summary_result.get("tags"), list) else []
    note["action_items"] = (
        summary_result.get("action_items", [])[:20] if isinstance(summary_result.get("action_items"), list) else []
    )
    note["detail_level"] = detail
    note["status"] = "processed"
    note["updated_at"] = utc_now()


def build_summary_prompt(note: dict[str, Any], preset: dict[str, str], capture_mode: dict[str, str], detail: str) -> str:
    detail = normalize_detail(detail)
    transcript = note.get("transcript", "")
    return f"""
Context:
- Source: {note.get("source", "unknown")}
- Capture mode: {capture_mode["name"]} - {capture_mode["description"]}
- Note preset: {preset["name"]} - {preset["description"]}
- Detail target: {detail}. {DETAIL_GUIDANCE[detail]}

Write the final note in this standardized markdown structure:

# Overview
Briefly explain what happened.

# Key Points
- Use grouped, skimmable bullets.

# Decisions
- Include only decisions that were actually made.

# Action Items
- Use "Owner: task - due date" when the transcript gives those details.
- If no owner or due date exists, keep the task but do not invent metadata.

# Open Questions
- Include unresolved questions or information that needs follow-up.

Rules:
- Preserve important names, dates, numbers, and commitments.
- Do not invent facts that are not in the transcript.
- Make the title specific, not generic.
- If the transcript is thin, say what can be safely inferred and keep the note short.

Transcript:
{transcript}
""".strip()


def fallback_summary(note: dict[str, Any], detail: str) -> str:
    preset = preset_by_id(note.get("preset_id", "general"))
    return "\n".join(
        [
            "# Overview",
            f"Audio saved from {note.get('source', 'unknown')} using the {preset['name']} preset.",
            "",
            "# Key Points",
            "- Transcription and AI notes are pending until models are configured in Settings.",
            f"- Original file: {note.get('original_filename', note.get('audio_filename', 'audio'))}",
            f"- Detail target: {detail}",
            "",
            "# Action Items",
            "- Configure transcription and summary models, then regenerate this note.",
        ]
    )


def openai_json(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def openai_multipart(
    url: str,
    api_key: str,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    file_mime: str,
) -> dict[str, Any]:
    boundary = f"----NotPlaud{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {file_mime}\r\n\r\n".encode())
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    request = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def plain_text_for_pdf(note: dict[str, Any]) -> list[str]:
    created = note.get("created_at", "")
    preset = preset_by_id(note.get("preset_id", "general"))["name"]
    lines = [
        note.get("title", "Untitled Recording"),
        f"Date: {created}",
        f"Preset: {preset}",
        f"Source: {note.get('source', 'unknown')}",
        "",
    ]
    for raw_line in note.get("summary", "").splitlines():
        line = raw_line.replace("#", "").replace("**", "").strip()
        lines.extend(textwrap.wrap(line, width=92) or [""])
    return lines


def make_pdf(note: dict[str, Any]) -> bytes:
    lines = plain_text_for_pdf(note)
    pages = [lines[index : index + 48] for index in range(0, len(lines), 48)] or [[]]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    page_ids: list[int] = []
    next_id = 4

    for page_lines in pages:
        stream = render_pdf_page(page_lines)
        stream_id = next_id
        objects[stream_id] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        next_id += 1
        page_id = next_id
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(stream_id).encode()
            + b" 0 R >>"
        )
        page_ids.append(page_id)
        next_id += 1

    kids = b" ".join(str(page_id).encode() + b" 0 R" for page_id in page_ids)
    objects[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"

    pdf = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(pdf)
        pdf.extend(f"{object_id} 0 obj\n".encode())
        pdf.extend(objects[object_id])
        pdf.extend(b"\nendobj\n")
    xref_at = len(pdf)
    pdf.extend(f"xref\n0 {max(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for object_id in range(1, max(objects) + 1):
        pdf.extend(f"{offsets.get(object_id, 0):010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {max(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return bytes(pdf)


def render_pdf_page(lines: list[str]) -> bytes:
    output = ["BT", "/F1 12 Tf", "50 748 Td", "15 TL"]
    for index, line in enumerate(lines):
        font_size = 16 if index == 0 else 12
        output.append(f"/F1 {font_size} Tf")
        output.append(f"({pdf_escape(line)}) Tj")
        output.append("T*")
    output.append("ET")
    return "\n".join(output).encode("latin-1", "replace")


def pdf_escape(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# --- Device sync ------------------------------------------------------------


def sync_device_config(settings: dict[str, Any]) -> dict[str, Any]:
    """Push mic mode, WiFi credentials, and the upload endpoint over BLE."""
    ssid = cached_ssid()
    password = ""
    if ssid:
        match = match_saved_network(ssid, settings.get("wifi_networks", []))
        if match:
            password = match.get("password", "")
    return push_config_sync(
        device_mode=settings.get("device_mode", "standard"),
        wifi_ssid=ssid,
        wifi_password=password,
        device_name=settings.get("device_name", "NotPlaud"),
        host=ingest_server.local_ip(),
        port=int(settings.get("ingest_port", 8788)),
        token=settings.get("ingest_token", ""),
        device_address=settings.get("ble_device_address") or None,
    )


def apply_ingest_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Start, restart, or stop the LAN receiver to match current settings."""
    if not settings.get("ingest_enabled", True):
        ingest_server.stop()
        return {"ok": True, "running": False}
    result = ingest_server.start(
        INCOMING_DIR,
        settings.get("ingest_token", ""),
        port=int(settings.get("ingest_port", 8788)),
        on_upload=on_device_upload,
    )
    result["running"] = ingest_server.is_running()
    return result


def on_device_upload(path: Path) -> None:
    """Called from the ingest thread once a device file has fully landed."""
    with STATE_LOCK:
        state = load_state()
        imported = scan_incoming(state, min_age=0)
        auto = state["settings"].get("auto_process", True)
        save_state(state)
    if auto:
        for note in imported:
            queue_note(note["id"])


def pick_file_dialog(kind: str = "model") -> str:
    if FILE_PICKER:
        return FILE_PICKER(kind)
    return ""


class NotPlaudHandler(BaseHTTPRequestHandler):
    server_version = "NotPlaud/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path in {"/", "/app"}:
                return self.serve_static(STATIC_DIR / "index.html")
            if path.startswith("/static/"):
                return self.serve_static(STATIC_DIR / path.removeprefix("/static/"))
            if path == "/api/bootstrap":
                with STATE_LOCK:
                    return self.send_json(public_state(load_state()))
            if path == "/api/device/scan":
                return self.send_json({"devices": scan_devices_sync()})
            if path == "/api/wifi/current":
                return self.send_json({"ssid": cached_ssid(ttl=0)})
            if path == "/api/usb/scan":
                with STATE_LOCK:
                    hint = load_state()["settings"].get("usb_volume_hint", "NOTPLAUD")
                return self.send_json({"volumes": usb_import.scan(hint)})
            note_match = re.fullmatch(r"/api/notes/([a-f0-9]+)/audio", path)
            if note_match:
                return self.serve_note_audio(note_match.group(1))
            note_match = re.fullmatch(r"/api/notes/([a-f0-9]+)/raw", path)
            if note_match:
                return self.serve_note_raw(note_match.group(1))
            note_match = re.fullmatch(r"/api/notes/([a-f0-9]+)/pdf", path)
            if note_match:
                return self.serve_note_pdf(note_match.group(1))
            self.send_error(404, "Not found")
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json()
            if path == "/api/scan":
                return self.handle_scan()
            if path == "/api/notes":
                return self.handle_upload(payload)
            if path == "/api/folders":
                return self.handle_create_folder(payload)
            if path == "/api/wifi-networks":
                return self.handle_add_wifi(payload)
            if path == "/api/device/push-config":
                return self.handle_push_config()
            if path == "/api/usb/import":
                return self.handle_usb_import()
            if path == "/api/ingest/restart":
                return self.handle_ingest_restart()
            if path == "/api/pick-file":
                return self.handle_pick_file(payload)
            note_match = re.fullmatch(r"/api/notes/([a-f0-9]+)/rename", path)
            if note_match:
                return self.handle_rename(note_match.group(1), payload)
            note_match = re.fullmatch(r"/api/notes/([a-f0-9]+)/reprocess", path)
            if note_match:
                return self.handle_reprocess(note_match.group(1), payload)
            self.send_error(404, "Not found")
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def do_PATCH(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            payload = self.read_json()
            if path == "/api/settings":
                return self.handle_settings(payload)
            note_match = re.fullmatch(r"/api/notes/([a-f0-9]+)", path)
            if note_match:
                return self.handle_note_patch(note_match.group(1), payload)
            wifi_match = re.fullmatch(r"/api/wifi-networks/([A-Za-z0-9_-]+)", path)
            if wifi_match:
                return self.handle_update_wifi(wifi_match.group(1), payload)
            self.send_error(404, "Not found")
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            note_match = re.fullmatch(r"/api/notes/([a-f0-9]+)", path)
            if note_match:
                return self.handle_delete_note(note_match.group(1))
            folder_match = re.fullmatch(r"/api/folders/([A-Za-z0-9_-]+)", path)
            if folder_match:
                return self.handle_delete_folder(folder_match.group(1))
            wifi_match = re.fullmatch(r"/api/wifi-networks/([A-Za-z0-9_-]+)", path)
            if wifi_match:
                return self.handle_delete_wifi(wifi_match.group(1))
            self.send_error(404, "Not found")
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_scan(self) -> None:
        with STATE_LOCK:
            state = load_state()
            imported = scan_incoming(state)
            auto = state["settings"].get("auto_process", True)
            save_state(state)
            payload = public_state(state) | {"imported": len(imported)}
        if auto:
            for note in imported:
                queue_note(note["id"])
        self.send_json(payload)

    def handle_upload(self, payload: dict[str, Any]) -> None:
        with STATE_LOCK:
            state = load_state()
            note = create_note_from_upload(state, payload)
            auto = state["settings"].get("auto_process", True)
            save_state(state)
            response = public_state(state) | {"created_note_id": note["id"]}
        if auto:
            queue_note(note["id"])
        self.send_json(response, status=201)

    def handle_create_folder(self, payload: dict[str, Any]) -> None:
        name = str(payload.get("name", "")).strip()[:48]
        if not name:
            return self.send_json({"error": "Folder name is required."}, status=400)
        with STATE_LOCK:
            state = load_state()
            folder = {"id": normalize_filename(name).lower()[:40] or uuid.uuid4().hex[:8], "name": name, "created_at": utc_now()}
            existing_ids = {item["id"] for item in state["folders"]}
            while folder["id"] in existing_ids:
                folder["id"] = f"{folder['id']}-{uuid.uuid4().hex[:4]}"
            state["folders"].append(folder)
            save_state(state)
            self.send_json(public_state(state), status=201)

    def handle_rename(self, note_id: str, payload: dict[str, Any]) -> None:
        title = str(payload.get("title", "")).strip()[:120]
        if not title:
            return self.send_json({"error": "Title is required."}, status=400)
        with STATE_LOCK:
            state = load_state()
            note = find_note(state, note_id)
            if not note:
                return self.send_json({"error": "Note not found."}, status=404)
            note["title"] = title
            note["updated_at"] = utc_now()
            save_state(state)
            self.send_json(public_state(state))

    def handle_reprocess(self, note_id: str, payload: dict[str, Any]) -> None:
        detail = str(payload.get("detail_level", "") or "").strip() or None
        force = bool(payload.get("force_transcribe"))
        with STATE_LOCK:
            state = load_state()
            note = find_note(state, note_id)
            if not note:
                return self.send_json({"error": "Note not found."}, status=404)
            if force:
                note["transcript"] = ""
                note["raw_transcript"] = ""
            note["summaries"] = {}
            save_state(state)
            response = public_state(state)
        queue_note(note_id, detail)
        self.send_json(response)

    def handle_settings(self, payload: dict[str, Any]) -> None:
        allowed = set(DEFAULT_SETTINGS.keys())
        with STATE_LOCK:
            state = load_state()
            before = dict(state["settings"])
            for key, value in payload.items():
                if key not in allowed:
                    continue
                if key in SECRET_SETTINGS_KEYS and value == "********":
                    continue
                # WiFi networks have their own endpoints; a masked round-trip
                # through here would wipe stored passwords.
                if key == "wifi_networks":
                    continue
                if key == "default_detail":
                    value = normalize_detail(str(value))
                state["settings"][key] = value
            state["settings"] = migrate_settings(state["settings"])
            save_state(state)
            settings = jobs.snapshot(state["settings"])

        ingest_changed = (
            before.get("ingest_enabled") != settings.get("ingest_enabled")
            or before.get("ingest_port") != settings.get("ingest_port")
            or before.get("ingest_token") != settings.get("ingest_token")
        )
        if ingest_changed:
            apply_ingest_settings(settings)

        with STATE_LOCK:
            result = public_state(load_state())

        if settings.get("auto_wifi_sync") and (
            payload.get("device_mode") or before.get("device_mode") != settings.get("device_mode")
        ):
            result["devicePush"] = sync_device_config(settings)
        self.send_json(result)

    def handle_note_patch(self, note_id: str, payload: dict[str, Any]) -> None:
        queue_detail: str | None = None
        with STATE_LOCK:
            state = load_state()
            note = find_note(state, note_id)
            if not note:
                return self.send_json({"error": "Note not found."}, status=404)
            if "folder_id" in payload:
                folder_ids = {folder["id"] for folder in state["folders"]}
                note["folder_id"] = payload["folder_id"] if payload["folder_id"] in folder_ids else "inbox"
            if "detail_level" in payload:
                detail = normalize_detail(str(payload["detail_level"]))
                note["detail_level"] = detail
                cached = note.get("summaries", {}).get(detail)
                if cached:
                    note["summary"] = cached
                else:
                    queue_detail = detail
            if "preset_id" in payload:
                note["preset_id"] = payload["preset_id"]
            note["updated_at"] = utc_now()
            save_state(state)
            response = public_state(state)
        if queue_detail:
            queue_note(note_id, queue_detail)
            with STATE_LOCK:
                response = public_state(load_state())
        self.send_json(response)

    def handle_delete_note(self, note_id: str) -> None:
        with STATE_LOCK:
            state = load_state()
            note = find_note(state, note_id)
            if not note:
                return self.send_json({"error": "Note not found."}, status=404)
            state["notes"] = [item for item in state["notes"] if item["id"] != note_id]
            audio_path = AUDIO_DIR / note.get("audio_filename", "")
            if audio_path.exists() and audio_path.is_file():
                audio_path.unlink()
            save_state(state)
            self.send_json(public_state(state))

    def handle_delete_folder(self, folder_id: str) -> None:
        if folder_id == "inbox":
            return self.send_json({"error": "Inbox cannot be deleted."}, status=400)
        with STATE_LOCK:
            state = load_state()
            state["folders"] = [folder for folder in state["folders"] if folder["id"] != folder_id]
            for note in state["notes"]:
                if note.get("folder_id") == folder_id:
                    note["folder_id"] = "inbox"
            save_state(state)
            self.send_json(public_state(state))

    def handle_add_wifi(self, payload: dict[str, Any]) -> None:
        try:
            network = normalize_network(payload)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, status=400)
        with STATE_LOCK:
            state = load_state()
            networks = state["settings"].setdefault("wifi_networks", [])
            networks = [item for item in networks if item.get("id") != network["id"]]
            networks.append(network)
            state["settings"]["wifi_networks"] = networks
            save_state(state)
            result = public_state(state)
            settings = jobs.snapshot(state["settings"])
        if settings.get("auto_wifi_sync"):
            result["devicePush"] = sync_device_config(settings)
        self.send_json(result, status=201)

    def handle_update_wifi(self, network_id: str, payload: dict[str, Any]) -> None:
        with STATE_LOCK:
            state = load_state()
            networks = state["settings"].setdefault("wifi_networks", [])
            updated = None
            for item in networks:
                if item.get("id") == network_id:
                    if "ssid" in payload:
                        item["ssid"] = str(payload["ssid"]).strip()[:64]
                    if "password" in payload and payload["password"] != "********":
                        item["password"] = str(payload["password"])
                    if "priority" in payload:
                        item["priority"] = int(payload["priority"] or 0)
                    if "auto_connect" in payload:
                        item["auto_connect"] = bool(payload["auto_connect"])
                    updated = item
                    break
            if not updated:
                return self.send_json({"error": "Network not found."}, status=404)
            save_state(state)
            result = public_state(state)
            settings = jobs.snapshot(state["settings"])
        if settings.get("auto_wifi_sync"):
            result["devicePush"] = sync_device_config(settings)
        self.send_json(result)

    def handle_delete_wifi(self, network_id: str) -> None:
        with STATE_LOCK:
            state = load_state()
            networks = state["settings"].setdefault("wifi_networks", [])
            state["settings"]["wifi_networks"] = [item for item in networks if item.get("id") != network_id]
            save_state(state)
            self.send_json(public_state(state))

    def handle_push_config(self) -> None:
        with STATE_LOCK:
            settings = jobs.snapshot(load_state()["settings"])
        result = sync_device_config(settings)
        with STATE_LOCK:
            payload = public_state(load_state())
        self.send_json({"ok": result.get("ok", False), "result": result, **payload})

    def handle_usb_import(self) -> None:
        with STATE_LOCK:
            state = load_state()
            hint = state["settings"].get("usb_volume_hint", "NOTPLAUD")
            seen = {note.get("original_filename", "") for note in state["notes"]}
        result = usb_import.import_all(INCOMING_DIR, seen, hint)
        if not result.get("ok"):
            return self.send_json({"error": result.get("error", "USB import failed.")}, status=404)

        with STATE_LOCK:
            state = load_state()
            imported = scan_incoming(state)
            auto = state["settings"].get("auto_process", True)
            save_state(state)
            payload = public_state(state) | {"imported": len(imported), "copied": len(result["copied"])}
        if auto:
            for note in imported:
                queue_note(note["id"])
        self.send_json(payload)

    def handle_ingest_restart(self) -> None:
        with STATE_LOCK:
            settings = jobs.snapshot(load_state()["settings"])
        result = apply_ingest_settings(settings)
        with STATE_LOCK:
            payload = public_state(load_state())
        self.send_json({"ingestResult": result, **payload})

    def handle_pick_file(self, payload: dict[str, Any]) -> None:
        kind = payload.get("kind", "model")
        path = pick_file_dialog(kind)
        self.send_json({"path": path})

    def serve_note_audio(self, note_id: str) -> None:
        with STATE_LOCK:
            state = load_state()
            note = find_note(state, note_id)
            if not note:
                return self.send_json({"error": "Note not found."}, status=404)
            audio_path = AUDIO_DIR / note["audio_filename"]
        return self.serve_file(audio_path, note.get("mime_type") or mime_for_path(audio_path))

    def serve_note_raw(self, note_id: str) -> None:
        with STATE_LOCK:
            state = load_state()
            note = find_note(state, note_id)
            if not note:
                return self.send_json({"error": "Note not found."}, status=404)
            self.send_json(
                {
                    "title": note.get("title"),
                    "transcript": note.get("transcript", ""),
                    "raw_transcript": note.get("raw_transcript", ""),
                    "status": note.get("status", ""),
                    "error": note.get("error", ""),
                }
            )

    def serve_note_pdf(self, note_id: str) -> None:
        with STATE_LOCK:
            state = load_state()
            note = find_note(state, note_id)
            if not note:
                return self.send_json({"error": "Note not found."}, status=404)
            payload = make_pdf(note)
            filename = normalize_filename(note.get("title", "note")) + ".pdf"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def serve_static(self, path: Path) -> None:
        resolved = path.resolve()
        if not str(resolved).startswith(str(STATIC_DIR.resolve())) or not resolved.exists():
            return self.send_error(404, "Not found")
        return self.serve_file(resolved, mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")

    def serve_file(self, path: Path, content_type: str) -> None:
        resolved = path.resolve()
        allowed_roots = [STATIC_DIR.resolve(), AUDIO_DIR.resolve()]
        if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
            return self.send_error(403, "Forbidden")
        if not resolved.exists() or not resolved.is_file():
            return self.send_error(404, "Not found")
        payload = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        if "/api/bootstrap" in str(args):  # keep the poll out of the log
            return
        print(f"[{utc_now()}] {self.address_string()} {format % args}")


def start_server(host: str, port: int) -> ThreadingHTTPServer:
    global SERVER
    ensure_directories()
    jobs.configure(run_note_job)
    jobs.start_worker()

    with STATE_LOCK:
        state = load_state()
        settings = jobs.snapshot(state["settings"])
        # Anything left mid-flight from a previous run gets picked up again.
        stale = [note["id"] for note in state["notes"] if note.get("status") in {"queued", "processing"}]

    ingest_result = apply_ingest_settings(settings)
    if ingest_result.get("ok"):
        print(f"Device uploads: http://{ingest_result.get('host')}:{ingest_result.get('port')}/upload")
    else:
        print(f"Device upload server not started: {ingest_result.get('error')}")

    if settings.get("auto_process", True):
        for note_id in stale:
            jobs.enqueue(note_id)

    server = ThreadingHTTPServer((host, port), NotPlaudHandler)
    SERVER = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run(host: str, port: int) -> None:
    start_server(host, port)
    print(f"NotPlaud running at http://{host}:{port}")
    print(f"Device transfer folder: {INCOMING_DIR}")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping NotPlaud.")
        ingest_server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="NotPlaud local audio notes server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
