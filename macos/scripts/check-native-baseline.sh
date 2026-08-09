#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

if [ -x "$REPO_ROOT/.venv-macos/bin/python" ]; then
  PYTHON_BIN="$REPO_ROOT/.venv-macos/bin/python"
elif [ -x /opt/homebrew/bin/python3.13 ]; then
  PYTHON_BIN=/opt/homebrew/bin/python3.13
elif [ -x /opt/homebrew/bin/python3 ]; then
  PYTHON_BIN=/opt/homebrew/bin/python3
else
  PYTHON_BIN=python3
fi

cd "$REPO_ROOT"
exec "$PYTHON_BIN" -u -m unittest frigate.test.test_native_macos_baseline
