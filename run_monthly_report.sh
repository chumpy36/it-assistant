#!/bin/bash
# run_monthly_report.sh — wrapper for cron
# Usage: bash run_monthly_report.sh [--install] [--setup-gcal]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/venv_monthly"
PYTHON="$VENV/bin/python"
LOG="$SCRIPT_DIR/monthly_report.log"

if [[ "$1" == "--install" ]]; then
    echo "Creating virtualenv..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet \
        httpx \
        python-dotenv \
        reportlab \
        google-auth-oauthlib \
        google-api-python-client
    echo "Done. Now run: bash run_monthly_report.sh --setup-gcal"
    exit 0
fi

if [[ "$1" == "--setup-gcal" ]]; then
    "$PYTHON" "$SCRIPT_DIR/monthly_report.py" --setup-gcal
    exit 0
fi

# Normal run (called by cron)
echo "--- $(date) ---" >> "$LOG"
"$PYTHON" "$SCRIPT_DIR/monthly_report.py" >> "$LOG" 2>&1
echo "Exit: $?" >> "$LOG"
