#!/usr/bin/env python3
"""Launch NotPlaud as a native desktop application.

This is the entry point you actually want. `app.py` on its own is just the
local service; this wraps it in a real OS window with a native file picker, so
NotPlaud behaves like an application rather than a browser tab.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

import webview

import app as app_module
from app import start_server


def pick_free_port(preferred: int) -> int:
    """Use the preferred port, or the next free one if something else has it."""
    for candidate in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    return preferred


def create_file_picker(window: webview.Window):
    """Native "Browse…" dialog for choosing on-device model files."""

    def pick(kind: str = "summary") -> str:
        if kind == "transcription":
            # faster-whisper takes either a size name or a converted model
            # directory, so allow picking a folder.
            result = window.create_file_dialog(webview.FOLDER_DIALOG)
        else:
            result = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("GGUF models (*.gguf)", "All files (*.*)"),
            )
        if result:
            return result[0] if isinstance(result, (list, tuple)) else str(result)
        return ""

    return pick


def run_desktop(host: str, port: int, fullscreen: bool = False, debug: bool = False) -> None:
    port = pick_free_port(port)
    start_server(host, port)
    time.sleep(0.4)  # let the socket bind before the window loads the page

    window = webview.create_window(
        "NotPlaud",
        f"http://{host}:{port}",
        width=1440,
        height=920,
        min_size=(1024, 680),
        fullscreen=fullscreen,
        text_select=True,
    )

    app_module.FILE_PICKER = create_file_picker(window)

    try:
        webview.start(debug=debug)
    finally:
        # Stop listening on the LAN when the window closes.
        import ingest_server

        ingest_server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="NotPlaud desktop application.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--debug", action="store_true", help="open the web inspector")
    args = parser.parse_args()
    run_desktop(args.host, args.port, args.fullscreen, args.debug)


if __name__ == "__main__":
    main()
