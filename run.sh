#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "No virtual environment found."
    read -p "Create one and install requirements? [y/N] " answer
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
        echo "Aborted."
        exit 1
    fi
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "Installing requirements..."
    "$VENV_DIR/bin/pip" install -q -r requirements.txt
    echo "Done."
fi

source "$VENV_DIR/bin/activate"
exec python cli.py web
