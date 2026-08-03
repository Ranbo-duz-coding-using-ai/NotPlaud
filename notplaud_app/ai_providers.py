"""AI transcription and summarization — local models or cloud APIs."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_whisper_model = None
_whisper_model_key = ""
_llama_model = None
_llama_model_key = ""


def normalize_detail(detail: str) -> str:
    aliases = {"short": "low", "normal": "medium", "long": "high"}
    normalized = aliases.get(detail, detail)
    return normalized if normalized in {"low", "medium", "high", "ultra"} else "medium"


def api_key_for(settings: dict[str, Any], prefix: str) -> str:
    stored = settings.get(f"{prefix}_api_key", "")
    if stored and stored != "********":
        return stored
    env_name = settings.get(f"{prefix}_api_key_env") or settings.get("api_key_env") or "OPENAI_API_KEY"
    import os

    return os.environ.get(env_name, "")


def transcribe_audio(audio_path: Path, settings: dict[str, Any]) -> tuple[str, str, str]:
    source = settings.get("transcription_source", "local")
    if source == "local":
        return transcribe_local(audio_path, settings)
    return transcribe_api(audio_path, settings)


def transcribe_local(audio_path: Path, settings: dict[str, Any]) -> tuple[str, str, str]:
    model_ref = (settings.get("local_transcription_model_path") or "base").strip()
    if not model_ref:
        return "", "", "No local transcription model configured. Select a Whisper model in Settings."

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return (
            "",
            "",
            "faster-whisper is not installed. Run: pip install faster-whisper",
        )

    global _whisper_model, _whisper_model_key
    if _whisper_model is None or _whisper_model_key != model_ref:
        model_path = model_ref if Path(model_ref).exists() else model_ref
        _whisper_model = WhisperModel(model_path, device="auto", compute_type="auto")
        _whisper_model_key = model_ref

    try:
        segments, info = _whisper_model.transcribe(str(audio_path), beam_size=5, vad_filter=True)
        parts = [segment.text.strip() for segment in segments if segment.text.strip()]
        transcript = " ".join(parts).strip()
        raw = json.dumps({"language": info.language, "duration": info.duration, "segments": len(parts)}, indent=2)
        if not transcript:
            return "", raw, "Local transcription returned no text."
        return transcript, raw, ""
    except Exception as exc:
        return "", "", f"Local transcription failed: {exc}"


def transcribe_api(audio_path: Path, settings: dict[str, Any]) -> tuple[str, str, str]:
    provider = settings.get("transcription_api_provider", "openai")
    if provider == "openai":
        return transcribe_openai(audio_path, settings)
    if provider == "google":
        return transcribe_google(audio_path, settings)
    return "", "", f"Unsupported transcription API provider: {provider}"


def transcribe_openai(audio_path: Path, settings: dict[str, Any]) -> tuple[str, str, str]:
    key = api_key_for(settings, "transcription")
    if not key:
        return "", "", "Missing OpenAI API key for transcription."

    from app import mime_for_path, openai_multipart

    fields = {
        "model": settings.get("transcription_api_model") or "whisper-1",
        "response_format": "json",
    }
    try:
        response = openai_multipart(
            "https://api.openai.com/v1/audio/transcriptions",
            key,
            fields,
            "file",
            audio_path.name,
            audio_path.read_bytes(),
            mime_for_path(audio_path),
        )
    except Exception as exc:
        return "", "", f"Transcription failed: {exc}"

    transcript = response.get("text", "").strip()
    if not transcript:
        return "", json.dumps(response, indent=2), "Transcription returned no text."
    return transcript, json.dumps(response, indent=2, ensure_ascii=False), ""


def transcribe_google(audio_path: Path, settings: dict[str, Any]) -> tuple[str, str, str]:
    key = api_key_for(settings, "transcription")
    if not key:
        return "", "", "Missing Google API key for transcription."

    import base64

    model = settings.get("transcription_api_model") or "gemini-2.0-flash"
    mime = "audio/wav" if audio_path.suffix.lower() == ".wav" else "audio/mpeg"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "Transcribe this audio verbatim. Return only the transcript text."},
                    {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio_path.read_bytes()).decode()}},
                ]
            }
        ]
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        response = http_json(url, payload, headers={"Content-Type": "application/json"})
        text = (
            response.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
        if not text:
            return "", json.dumps(response, indent=2), "Google transcription returned no text."
        return text, json.dumps(response, indent=2, ensure_ascii=False), ""
    except Exception as exc:
        return "", "", f"Google transcription failed: {exc}"


def summarize_transcript(note: dict[str, Any], settings: dict[str, Any], detail: str) -> tuple[dict[str, Any], str]:
    source = settings.get("summary_source", "local")
    if source == "local":
        return summarize_local(note, settings, detail)
    return summarize_api(note, settings, detail)


def summarize_local(note: dict[str, Any], settings: dict[str, Any], detail: str) -> tuple[dict[str, Any], str]:
    model_path = (settings.get("local_summary_model_path") or "").strip()
    if not model_path:
        return {}, "No local summary model configured. Select a .gguf model file in Settings."
    if not Path(model_path).exists():
        return {}, f"Local summary model not found: {model_path}"

    try:
        from llama_cpp import Llama
    except ImportError:
        return {}, "llama-cpp-python is not installed. Run: pip install llama-cpp-python"

    global _llama_model, _llama_model_key
    if _llama_model is None or _llama_model_key != model_path:
        _llama_model = Llama(model_path=model_path, n_ctx=8192, verbose=False)
        _llama_model_key = model_path

    from app import build_summary_prompt, mode_by_id, preset_by_id

    preset = preset_by_id(note.get("preset_id", "general"))
    capture_mode = mode_by_id(note.get("capture_mode_id", "standard"))
    user_prompt = build_summary_prompt(note, preset, capture_mode, normalize_detail(detail))
    system_prompt = (
        "You turn raw transcripts into standardized, useful notes. "
        "Return strict JSON only. No markdown fences. "
        'The JSON shape must be: '
        '{"title":"short title","summary_markdown":"markdown note","tags":["tag"],"action_items":["task"]}.'
    )

    try:
        response = _llama_model.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
        content = response["choices"][0]["message"]["content"]
        parsed = extract_json(content)
        if not isinstance(parsed, dict):
            return {"summary_markdown": content}, ""
        return parsed, ""
    except Exception as exc:
        return {}, f"Local summary failed: {exc}"


def summarize_api(note: dict[str, Any], settings: dict[str, Any], detail: str) -> tuple[dict[str, Any], str]:
    provider = settings.get("summary_api_provider", "openai")
    if provider == "openai":
        return summarize_openai(note, settings, detail)
    if provider == "google":
        return summarize_google(note, settings, detail)
    if provider == "anthropic":
        return summarize_anthropic(note, settings, detail)
    return {}, f"Unsupported summary API provider: {provider}"


def summarize_openai(note: dict[str, Any], settings: dict[str, Any], detail: str) -> tuple[dict[str, Any], str]:
    key = api_key_for(settings, "summary")
    if not key:
        return {}, "Missing OpenAI API key for summary."

    from app import build_summary_prompt, mode_by_id, openai_json, preset_by_id

    preset = preset_by_id(note.get("preset_id", "general"))
    capture_mode = mode_by_id(note.get("capture_mode_id", "standard"))
    system_prompt = (
        "You turn raw transcripts into standardized, useful notes. "
        "Return strict JSON only. No markdown fences. "
        'The JSON shape must be: '
        '{"title":"short title","summary_markdown":"markdown note","tags":["tag"],"action_items":["task"]}.'
    )
    user_prompt = build_summary_prompt(note, preset, capture_mode, normalize_detail(detail))
    payload = {
        "model": settings.get("summary_api_model") or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    try:
        response = openai_json("https://api.openai.com/v1/chat/completions", key, payload)
        content = response["choices"][0]["message"]["content"]
        parsed = extract_json(content)
        if not isinstance(parsed, dict):
            return {"summary_markdown": content}, ""
        return parsed, ""
    except Exception as exc:
        return {}, f"Summary failed: {exc}"


def summarize_google(note: dict[str, Any], settings: dict[str, Any], detail: str) -> tuple[dict[str, Any], str]:
    key = api_key_for(settings, "summary")
    if not key:
        return {}, "Missing Google API key for summary."

    from app import build_summary_prompt, mode_by_id, preset_by_id

    preset = preset_by_id(note.get("preset_id", "general"))
    capture_mode = mode_by_id(note.get("capture_mode_id", "standard"))
    user_prompt = build_summary_prompt(note, preset, capture_mode, normalize_detail(detail))
    model = settings.get("summary_api_model") or "gemini-2.0-flash"
    payload = {
        "contents": [{"parts": [{"text": user_prompt}]}],
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "Return strict JSON only. Shape: "
                        '{"title":"short title","summary_markdown":"markdown note","tags":["tag"],"action_items":["task"]}'
                    )
                }
            ]
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        response = http_json(url, payload, headers={"Content-Type": "application/json"})
        content = (
            response.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
        parsed = extract_json(content)
        if not isinstance(parsed, dict):
            return {"summary_markdown": content}, ""
        return parsed, ""
    except Exception as exc:
        return {}, f"Google summary failed: {exc}"


def summarize_anthropic(note: dict[str, Any], settings: dict[str, Any], detail: str) -> tuple[dict[str, Any], str]:
    key = api_key_for(settings, "summary")
    if not key:
        return {}, "Missing Anthropic API key for summary."

    from app import build_summary_prompt, mode_by_id, preset_by_id

    preset = preset_by_id(note.get("preset_id", "general"))
    capture_mode = mode_by_id(note.get("capture_mode_id", "standard"))
    user_prompt = build_summary_prompt(note, preset, capture_mode, normalize_detail(detail))
    payload = {
        "model": settings.get("summary_api_model") or "claude-3-5-haiku-20241022",
        "max_tokens": 4096,
        "system": (
            "Return strict JSON only. No markdown fences. "
            'Shape: {"title":"short title","summary_markdown":"markdown note","tags":["tag"],"action_items":["task"]}'
        ),
        "messages": [{"role": "user", "content": user_prompt}],
    }
    try:
        response = http_json(
            "https://api.anthropic.com/v1/messages",
            payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )
        blocks = response.get("content", [])
        content = next((block.get("text", "") for block in blocks if block.get("type") == "text"), "").strip()
        parsed = extract_json(content)
        if not isinstance(parsed, dict):
            return {"summary_markdown": content}, ""
        return parsed, ""
    except Exception as exc:
        return {}, f"Anthropic summary failed: {exc}"


def http_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers=headers or {})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_json(content: str) -> Any:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        return json.loads(match.group(0))
