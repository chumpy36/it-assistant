#!/usr/bin/env python3
"""Delete Synology container-stopped notification tickets from Syncro.
Runs on the NAS via cron — uses only stdlib (no pip required)."""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ENV_PATH = Path("/volume1/docker/syncro-todoist-assistant/.env")
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
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


def api_get(path, params=None):
    url = f"{BASE}{path}"
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def api_delete(path):
    req = urllib.request.Request(f"{BASE}{path}", method="DELETE", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def is_nas_notification(subject: str) -> bool:
    s = subject.lower()
    return ("synology" in s or "hitnas" in s or "container manager" in s) and \
           ("stopped" in s or "unexpected" in s)


def main():
    data = api_get("/tickets", {"q": "container manager stopped"})
    tickets = data.get("tickets", [])

    deleted = 0
    for t in tickets:
        subj = t.get("subject") or ""
        if not is_nas_notification(subj):
            continue
        status = api_delete(f"/tickets/{t['id']}")
        if status in (200, 204):
            print(f"Deleted #{t.get('number')}: {subj}", flush=True)
            deleted += 1
        else:
            print(f"Failed #{t.get('number')}: HTTP {status}", flush=True)

    if deleted:
        print(f"Cleaned up {deleted} ticket(s).", flush=True)


if __name__ == "__main__":
    main()
