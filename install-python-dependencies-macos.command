#!/bin/sh
# Install/check Python, tkinter, and Python Pillow for the EDOPro HD Pics Downloader.
# macOS helper. Double-click this .command file or run it from Terminal.

set -eu

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

cd "$(dirname "$0")"

MIN_PYTHON="3.9"
PYTHON_CMD=""

pause_exit() {
    printf "Press Enter to exit."
    read -r _ || true
}

python_is_usable() {
    cmd="$1"
    command -v "$cmd" >/dev/null 2>&1 || return 1
    "$cmd" - "$MIN_PYTHON" <<'PY' >/dev/null 2>&1
import sys
minimum = tuple(int(part) for part in sys.argv[1].split('.'))
raise SystemExit(0 if sys.version_info[:2] >= minimum else 1)
PY
}

find_python() {
    for cmd in python3.12 python3 python; do
        if python_is_usable "$cmd"; then
            PYTHON_CMD="$cmd"
            return 0
        fi
    done
    return 1
}

install_pillow_with_pip() {
    echo "Installing Python Pillow with pip..."
    if "$PYTHON_CMD" -m pip install --user --upgrade Pillow; then
        return 0
    fi

    echo "Normal user pip install failed; trying Homebrew/external Python fallback..."
    "$PYTHON_CMD" -m pip install --user --break-system-packages --upgrade Pillow
}

echo "EDOPro HD Pics Downloader dependency installer (macOS)"
echo "Recommended: Python 3.12, tkinter, Python Pillow"
echo "Minimum usable Python: $MIN_PYTHON"
echo ""

if find_python; then
    echo "Found Python: $($PYTHON_CMD --version)"
else
    echo "No usable Python was found."
    if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew was not found."
        echo "Recommended: install Homebrew from https://brew.sh, then run this file again."
        echo "Alternative: install Python 3.12 from https://www.python.org/downloads/ and then run:"
        echo "python3.12 -m pip install --upgrade Pillow"
        echo ""
        pause_exit
        exit 1
    fi

    echo "Installing Python 3.12 with Homebrew..."
    brew install python@3.12

    if ! find_python; then
        echo "Python was not found after installation."
        pause_exit
        exit 1
    fi
    echo "Found Python: $($PYTHON_CMD --version)"
fi

if ! "$PYTHON_CMD" - <<'PY'
import tkinter
print("tkinter available")
PY
then
    if ! command -v brew >/dev/null 2>&1; then
        echo "tkinter is missing and Homebrew was not found. Install Python 3.12 from python.org or install Homebrew."
        pause_exit
        exit 1
    fi

    echo "Installing tkinter support with Homebrew..."
    brew install python-tk@3.12 || true
    "$PYTHON_CMD" - <<'PY'
import tkinter
print("tkinter available")
PY
fi

if "$PYTHON_CMD" - <<'PY'
import PIL
print("Python Pillow already available")
PY
then
    :
else
    echo "Upgrading pip..."
    "$PYTHON_CMD" -m pip install --user --upgrade pip || true
    install_pillow_with_pip
fi

echo ""
echo "Done. You can run the downloader with:"
echo "$PYTHON_CMD EDOPro-Standard-Rush-HD-Pics-Downloader.py"
echo ""
pause_exit
