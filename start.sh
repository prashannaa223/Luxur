#!/usr/bin/env bash
# LUXUR launcher for Linux (testing only; Windows is the supported platform).
# First run: creates a Python environment and installs everything (a few minutes).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/L.U.X.U.R.O.S - Copy"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "Python 3.10 or newer is required."; exit 1; }

if [ ! -x .venv/bin/python ]; then
    echo "Setting up LUXUR for the first time. This takes a few minutes..."
    "$PY" -m venv .venv
fi

if ! cmp -s Backend/Requirement.txt .venv/installed.txt; then
    echo "Installing requirements..."
    .venv/bin/python -m pip install --upgrade pip --quiet
    .venv/bin/python -m pip install -r Backend/Requirement.txt
    cp Backend/Requirement.txt .venv/installed.txt
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Put your API keys into $(pwd)/.env, then run ./start.sh again."
    exit 0
fi

exec .venv/bin/python main.py "$@"
