"""LAN receiver for NotPlaud device uploads.

The UI server stays bound to localhost. This second server is the only thing
listening on the LAN, and it accepts exactly three requests from the ESP32-S3:

    GET  /ping                      -> handshake + token check
    GET  /manifest                  -> filenames already received
    POST /upload?name=<file>        -> raw audio body, written atomically

Every request must carry the shared token (header ``X-NotPlaud-Token`` or a
``token`` query parameter). The token is generated once and handed to the
device over BLE, so a stray host on the same WiFi cannot push files.
"""

from __future__ import annotations

import re
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MB ceiling per file
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

_server: ThreadingHTTPServer | None = None
_incoming_dir: Path | None = None
_token: str = ""
_on_upload: Callable[[Path], None] | None = None
_lock = threading.Lock()


def local_ip() -> str:
    """Best-effort LAN address of this machine (no traffic is actually sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def safe_filename(name: str) -> str:
    cleaned = SAFE_NAME.sub("-", Path(name).name).strip("-.")
    return cleaned or "recording.wav"


class IngestHandler(BaseHTTPRequestHandler):
    server_version = "NotPlaudIngest/1.0"
    protocol_version = "HTTP/1.1"

    def _authorized(self, params: dict[str, list[str]]) -> bool:
        if not _token:
            return True
        supplied = self.headers.get("X-NotPlaud-Token", "")
        if not supplied:
            supplied = (params.get("token") or [""])[0]
        return supplied == _token

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if not self._authorized(params):
            return self._reply(401, b'{"ok":false,"error":"bad token"}')

        if parsed.path == "/ping":
            return self._reply(200, b'{"ok":true,"service":"notplaud"}')

        if parsed.path == "/manifest":
            names = []
            if _incoming_dir and _incoming_dir.exists():
                names = sorted(p.name for p in _incoming_dir.iterdir() if p.is_file())
            payload = b'{"ok":true,"files":[' + b",".join(b'"' + n.encode() + b'"' for n in names) + b"]}"
            return self._reply(200, payload)

        self._reply(404, b'{"ok":false,"error":"not found"}')

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if not self._authorized(params):
            return self._reply(401, b'{"ok":false,"error":"bad token"}')

        if parsed.path != "/upload":
            return self._reply(404, b'{"ok":false,"error":"not found"}')

        if _incoming_dir is None:
            return self._reply(503, b'{"ok":false,"error":"not ready"}')

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return self._reply(400, b'{"ok":false,"error":"empty body"}')
        if length > MAX_UPLOAD_BYTES:
            return self._reply(413, b'{"ok":false,"error":"file too large"}')

        name = safe_filename((params.get("name") or ["recording.wav"])[0])
        target = _incoming_dir / name
        counter = 1
        while target.exists():
            target = _incoming_dir / f"{Path(name).stem}-{counter}{Path(name).suffix}"
            counter += 1

        # Write to .part first so the folder watcher never sees a half file.
        part = target.with_suffix(target.suffix + ".part")
        remaining = length
        try:
            with part.open("wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
            if remaining > 0:
                part.unlink(missing_ok=True)
                return self._reply(400, b'{"ok":false,"error":"truncated upload"}')
            part.replace(target)
        except OSError as exc:
            part.unlink(missing_ok=True)
            return self._reply(500, f'{{"ok":false,"error":"{exc}"}}'.encode())

        if _on_upload:
            try:
                _on_upload(target)
            except Exception as exc:  # never fail the device's upload on our own bug
                print(f"[notplaud] post-upload hook failed: {exc}")

        self._reply(200, f'{{"ok":true,"name":"{target.name}","bytes":{length}}}'.encode())

    def log_message(self, format: str, *args) -> None:
        print(f"[notplaud-ingest] {self.address_string()} {format % args}")


def start(
    incoming_dir: Path,
    token: str,
    port: int = 8788,
    host: str = "0.0.0.0",
    on_upload: Callable[[Path], None] | None = None,
) -> dict:
    """Start (or restart) the LAN ingest server."""
    global _server, _incoming_dir, _token, _on_upload

    with _lock:
        stop()
        _incoming_dir = incoming_dir
        _token = token
        _on_upload = on_upload
        try:
            server = ThreadingHTTPServer((host, port), IngestHandler)
        except OSError as exc:
            return {"ok": False, "error": f"Could not bind {host}:{port} — {exc}"}
        _server = server
        threading.Thread(target=server.serve_forever, name="notplaud-ingest", daemon=True).start()
        return {"ok": True, "host": local_ip(), "port": port}


def stop() -> None:
    global _server
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
        _server = None


def is_running() -> bool:
    return _server is not None
