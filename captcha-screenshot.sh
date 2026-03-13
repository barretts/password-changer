#!/usr/bin/env bash
set -euo pipefail

# Save a screenshot from the CamoFox browser to the host filesystem.
# Usage: ./camofox-screenshot.sh <tabId> [userId]
# Outputs the local file path on stdout.

TAB_ID="${1:?Usage: $0 <tabId> [userId]}"
USER_ID="${2:-default}"
CAMOFOX_URL="${CAMOFOX_URL:-http://127.0.0.1:9377}"

LOCAL_DIR="/tmp/pw-changer-screenshots"
mkdir -p "$LOCAL_DIR"
LOCAL_PATH="$LOCAL_DIR/captcha.png"

HTTP_CODE=$(curl -s -o "$LOCAL_PATH" -w "%{http_code}" \
  "${CAMOFOX_URL}/tabs/${TAB_ID}/screenshot?userId=${USER_ID}")

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "ERROR: Screenshot failed (HTTP $HTTP_CODE) for tab $TAB_ID" >&2
  exit 1
fi

if [[ ! -s "$LOCAL_PATH" ]]; then
  echo "ERROR: Screenshot file is empty" >&2
  exit 1
fi

echo "$LOCAL_PATH"
