#!/bin/bash
# Deploy script for syncro-todoist-assistant
# Runs compose down/build/up, then cleans up the NAS notification ticket in Syncro

set -e
DOCKER="/usr/local/bin/docker"
DIR="/volume1/docker/syncro-todoist-assistant"

cd "$DIR"

echo "[deploy] Pulling latest code from GitHub..."
git pull origin main

# Load SYNCRO_API_TOKEN from .env
SYNCRO_API_TOKEN=$(grep SYNCRO_API_TOKEN .env | cut -d= -f2 | tr -d '[:space:]')

echo "[deploy] Stopping containers..."
sudo $DOCKER compose down

echo "[deploy] Building..."
sudo $DOCKER compose build

echo "[deploy] Starting containers..."
sudo $DOCKER compose up -d

echo "[deploy] Waiting 90s for NAS notification to arrive in Syncro..."
sleep 90

echo "[deploy] Searching for notification ticket..."
RESPONSE=$(curl -s \
  -H "Authorization: Bearer $SYNCRO_API_TOKEN" \
  -H "Accept: application/json" \
  "https://hollandit.syncromsp.com/api/v1/tickets?q=it-assistant+stopped")

# Find tickets created in the last 5 minutes matching the notification pattern
NOW=$(date +%s)
echo "$RESPONSE" | python3 -c "
import sys, json, time
from datetime import datetime, timezone

data = json.load(sys.stdin)
tickets = data.get('tickets', [])
deleted = []

for t in tickets:
    subj = (t.get('subject') or '').lower()
    if 'it-assistant' not in subj and 'container' not in subj:
        continue
    if 'stopped' not in subj and 'unexpected' not in subj:
        continue
    created = t.get('created_at', '')
    try:
        # Parse created_at and check if within last 10 minutes
        dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age < 600:
            print(f\"MATCH:{t['id']}:{t['number']}:{t['subject']}\")
    except Exception as e:
        pass
" | while IFS=: read -r tag tid tnum tsubj; do
    if [ "$tag" = "MATCH" ]; then
        echo "[deploy] Deleting notification ticket #$tnum (id=$tid): $tsubj"
        curl -s -X DELETE \
          -H "Authorization: Bearer $SYNCRO_API_TOKEN" \
          "https://hollandit.syncromsp.com/api/v1/tickets/$tid" > /dev/null
        echo "[deploy] Deleted."
    fi
done

echo "[deploy] Done."
