#!/usr/bin/env python3
"""Delete Synology container-stopped notification tickets from Syncro."""

import os
import sys
import json
import httpx
from datetime import datetime, timezone
from pathlib import Path

# Load .env from same directory
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.environ.get("SYNCRO_API_TOKEN", "")
if not TOKEN:
    print("ERROR: SYNCRO_API_TOKEN not set", flush=True)
    sys.exit(1)

BASE = "https://hollandit.syncromsp.com/api/v1"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

SUBJECT_KEYWORDS = ["container manager stopped", "stopped unexpectedly", "it-assistant"]


def is_nas_notification(subject: str) -> bool:
    s = subject.lower()
    return (
        "synology" in s or "hitnas" in s or "container manager" in s
    ) and (
        "stopped" in s or "unexpected" in s
    )


def main():
    resp = httpx.get(f"{BASE}/tickets", params={"q": "container manager stopped"}, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    tickets = resp.json().get("tickets", [])

    deleted = 0
    for t in tickets:
        subj = t.get("subject") or ""
        if not is_nas_notification(subj):
            continue
        tid = t["id"]
        tnum = t.get("number")
        del_resp = httpx.delete(f"{BASE}/tickets/{tid}", headers=HEADERS, timeout=15)
        if del_resp.status_code in (200, 204):
            print(f"Deleted #{tnum}: {subj}", flush=True)
            deleted += 1
        else:
            print(f"Failed to delete #{tnum}: HTTP {del_resp.status_code}", flush=True)

    if deleted == 0:
        print("No NAS notification tickets found.", flush=True)
    else:
        print(f"Cleaned up {deleted} ticket(s).", flush=True)


if __name__ == "__main__":
    main()
