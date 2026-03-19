#!/bin/bash
# Mac-side deploy: push to GitHub, then trigger NAS deploy
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[push-deploy] ERROR: Uncommitted changes present. Commit first."
  exit 1
fi

echo "[push-deploy] Pushing to GitHub..."
git push origin main

echo "[push-deploy] Triggering NAS deploy..."
ssh nas 'sudo /volume1/docker/syncro-todoist-assistant/deploy.sh'
