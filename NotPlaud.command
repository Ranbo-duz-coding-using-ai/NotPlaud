#!/bin/bash
# Double-click this in Finder to open NotPlaud.
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo "No virtual environment found. Setting one up..."
  python3 -m venv .venv || exit 1
  .venv/bin/pip install -r notplaud_app/requirements.txt || exit 1
fi

exec .venv/bin/python notplaud_app/desktop.py
