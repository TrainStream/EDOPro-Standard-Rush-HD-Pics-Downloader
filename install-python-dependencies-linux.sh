#!/usr/bin/env sh
# Install/check Python, tkinter, and Python Pillow for the EDOPro HD Pics Downloader.
# Linux helper. Run from a terminal: sh install-python-dependencies-linux.sh

set -eu

MIN_PYTHON="3.9"
PYTHON_CMD=""

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

install_with_package_manager() {
    if command -v apt-get >/dev/null 2>&1; then
        echo "Using apt-get. sudo may ask for your password."
        sudo apt-get update
        sudo apt-get install -y python3.12 python3.12-venv python3.12-tk python3-pip python3-pil || \
            sudo apt-get install -y python3 python3-venv python3-tk python3-pip python3-pil
    elif command -v dnf >/dev/null 2>&1; then
        echo "Using dnf. sudo may ask for your password."
        sudo dnf install -y python3.12 python3.12-tkinter python3-pip python3-pillow || \
            sudo dnf install -y python3 python3-tkinter python3-pip python3-pillow
    elif command -v zypper >/dev/null 2>&1; then
        echo "Using zypper. sudo may ask for your password."
        sudo zypper install -y python312 python312-tk python312-pip || \
            sudo zypper install -y python3 python3-tk python3-pip python3-Pillow
    elif command -v pacman >/dev/null 2>&1; then
        echo "Using pacman. sudo may ask for your password."
        sudo pacman -S --needed python tk python-pip python-pillow
    else
        echo "No supported package manager found. Install Python, tkinter, and Python Pillow manually."
        exit 1
    fi
}

install_pillow_with_pip() {
    echo "Installing Python Pillow with pip..."
    if "$PYTHON_CMD" -m pip install --user --upgrade Pillow; then
        return 0
    fi

    echo "Normal user pip install failed; trying externally-managed Python fallback..."
    "$PYTHON_CMD" -m pip install --user --break-system-packages --upgrade Pillow
}

echo "EDOPro HD Pics Downloader dependency installer (Linux)"
echo "Recommended: Python 3.12, tkinter, Python Pillow"
echo "Minimum usable Python: $MIN_PYTHON"
echo ""

if find_python; then
    echo "Found Python: $($PYTHON_CMD --version)"
else
    echo "No usable Python was found. Trying to install Python and tkinter..."
    install_with_package_manager
    if ! find_python; then
        echo "Python was not found after installation. Your Linux distribution may not provide it directly."
        echo "Install Python 3.12 manually, then rerun this script."
        exit 1
    fi
    echo "Found Python: $($PYTHON_CMD --version)"
fi

if ! "$PYTHON_CMD" - <<'PY'
import tkinter
print("tkinter available")
PY
then
    echo "tkinter is missing for $PYTHON_CMD. Trying to install tkinter..."
    install_with_package_manager
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
