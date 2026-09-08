#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")"

echo "Starting XZY ODM troubleshooting strategy platform..."
echo "Open http://127.0.0.1:8000"

exec "${PYTHON_BIN:-python}" run.py
