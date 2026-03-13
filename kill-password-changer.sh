#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="/Users/ephem/lcode/password-changer"
DB_PATH="$WORKSPACE/passwords.db"

sq() {
  sqlite3 "$DB_PATH" ".timeout 30000" "$@"
}

RUNNER_PIDS="$(pgrep -f 'run-password-changer.sh' 2>/dev/null || true)"
AGENT_PIDS="$(pgrep -f 'agent --print.*password-changer' 2>/dev/null || true)"

ALL_PIDS="$RUNNER_PIDS $AGENT_PIDS"
ALL_PIDS="$(echo "$ALL_PIDS" | xargs)"

if [[ -z "$ALL_PIDS" ]]; then
  echo "No running password-changer processes found."
else
  echo "Sending SIGTERM to: $ALL_PIDS"
  for pid in $ALL_PIDS; do
    kill -TERM "$pid" 2>/dev/null || true
  done

  echo "Waiting 5s for graceful shutdown..."
  sleep 5

  for pid in $ALL_PIDS; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "Force killing $pid"
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
fi

in_progress="$(sq "SELECT COUNT(*) FROM entries WHERE status = 'in_progress';")"
if [[ "$in_progress" -gt 0 ]]; then
  echo "Marking $in_progress in-progress tasks as failed..."
  sq "UPDATE entries SET status = 'failed', error_message = 'Killed by kill-password-changer.sh', updated_at = datetime('now') WHERE status = 'in_progress';"
fi

echo "Done. Resume with: RESUME=1 ./run-password-changer.sh"
