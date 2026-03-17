#!/usr/bin/env bash
set -euo pipefail

CONCURRENCY="${1:-2}"
WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="$WORKSPACE/passwords.db"
TASK_FILE="$WORKSPACE/tasks.txt"
TEMPLATE="$WORKSPACE/prompt-template.md"
LOG_DIR="$WORKSPACE/logs/run-$(date +%Y%m%d-%H%M%S)"
FIFO="/tmp/pw-changer-fifo-$$"
CAMOFOX_URL="${CAMOFOX_URL:-http://127.0.0.1:9377}"

sq() {
  sqlite3 "$DB_PATH" ".timeout 30000" "$@"
}

build_prompt() {
  local task_id="$1"
  local row
  row="$(sq "SELECT url, base_url, root_url, username, old_password FROM entries WHERE id = $task_id;" -separator '|')"
  local url base_url root_url username old_password
  url="$(echo "$row" | cut -d'|' -f1)"
  base_url="$(echo "$row" | cut -d'|' -f2)"
  root_url="$(echo "$row" | cut -d'|' -f3)"
  username="$(echo "$row" | cut -d'|' -f4)"
  old_password="$(echo "$row" | cut -d'|' -f5)"

  local user_id="task-${task_id}"

  sed \
    -e "s|{{TASK_ID}}|$task_id|g" \
    -e "s|{{URL}}|$url|g" \
    -e "s|{{BASE_URL}}|${base_url:-$url}|g" \
    -e "s|{{ROOT_URL}}|${root_url:-}|g" \
    -e "s|{{USERNAME}}|$username|g" \
    -e "s|{{OLD_PASSWORD}}|$old_password|g" \
    -e "s|{{USER_ID}}|$user_id|g" \
    "$TEMPLATE"
}

run_task() {
  local task_id="$1"
  local domain
  domain="$(sq "SELECT domain FROM entries WHERE id = $task_id;")"
  local prompt_file="$LOG_DIR/task-${task_id}.prompt"
  local log_file="$LOG_DIR/task-${task_id}.log"
  local start_ts
  start_ts="$(date +%s)"

  sq "UPDATE entries SET status = 'in_progress', attempt_count = attempt_count + 1, last_attempt_at = datetime('now'), updated_at = datetime('now') WHERE id = $task_id;"

  build_prompt "$task_id" > "$prompt_file"

  agent --print --force --approve-mcps --trust \
    --model Composer-1.5 \
    --workspace "$WORKSPACE" \
    "$(cat "$prompt_file")" \
    > "$log_file" 2>&1 || true

  # Close any CamoFox tabs/sessions left by this agent
  local user_id="task-${task_id}"
  curl -sf -X DELETE "${CAMOFOX_URL}/sessions/${user_id}" \
    -H 'Content-Type: application/json' \
    -d "{\"userId\":\"${user_id}\"}" >/dev/null 2>&1 || true

  local end_ts elapsed status
  end_ts="$(date +%s)"
  elapsed=$(( end_ts - start_ts ))

  python3 "$WORKSPACE/update-db.py" "$task_id" "$log_file" || true

  status="$(sq "SELECT status FROM entries WHERE id = $task_id;")"
  echo "$task_id" >> "$LOG_DIR/_completed_tasks.txt"

  local completed_count ts
  completed_count="$(wc -l < "$LOG_DIR/_completed_tasks.txt" | tr -d ' ')"
  ts="$(date +%H:%M:%S)"
  echo "[$ts] [$completed_count/$TOTAL] task=$task_id domain=$domain status=$status elapsed=${elapsed}s" | tee -a "$LOG_DIR/_summary.log"
}

cleanup() {
  echo ""
  echo "Caught signal, cleaning up..."
  rm -f "$FIFO"
  if [[ ${#PIDS[@]} -gt 0 ]]; then
    for pid in "${PIDS[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
  fi
  sq "UPDATE entries SET status = 'pending', updated_at = datetime('now') WHERE status = 'in_progress';" || true
  echo "Marked in-progress tasks as pending. Resume with: RESUME=1 $0 $CONCURRENCY"
  exit 1
}

trap cleanup SIGINT SIGTERM

# --- Resume support ---
if [[ "${RESUME:-0}" == "1" ]] && [[ -f "$WORKSPACE/.last_log_dir" ]]; then
  PREV_LOG_DIR="$(cat "$WORKSPACE/.last_log_dir")"
  echo "Resuming from: $PREV_LOG_DIR"

  sq "UPDATE entries SET status = 'pending', updated_at = datetime('now') WHERE status = 'in_progress';"

  if [[ -f "$PREV_LOG_DIR/_completed_tasks.txt" ]]; then
    completed_ids="$(sort -u "$PREV_LOG_DIR/_completed_tasks.txt")"
  else
    completed_ids=""
  fi

  failed_ids="$(sq "SELECT id FROM entries WHERE status IN ('failed', 'blocked');" | sort -u)"

  {
    sq "SELECT id FROM entries WHERE status = 'pending' ORDER BY priority, id;"
    echo "$failed_ids"
  } | sort -u | while read -r id; do
    if [[ -n "$id" ]] && ! echo "$completed_ids" | grep -qx "$id" 2>/dev/null; then
      echo "$id"
    fi
  done > "$WORKSPACE/.resume_tasks.txt"

  TASK_SOURCE="$WORKSPACE/.resume_tasks.txt"
else
  TASK_SOURCE="$TASK_FILE"
  sq "SELECT id FROM entries WHERE status = 'pending' ORDER BY priority, id;" > "$WORKSPACE/.active_tasks.txt"
  TASK_SOURCE="$WORKSPACE/.active_tasks.txt"
fi

mkdir -p "$LOG_DIR"
echo "$LOG_DIR" > "$WORKSPACE/.last_log_dir"
cp "$TASK_SOURCE" "$LOG_DIR/_tasks.txt"

TOTAL="$(wc -l < "$TASK_SOURCE" | tr -d ' ')"
touch "$LOG_DIR/_completed_tasks.txt"

echo "=== Password Changer ==="
echo "Tasks: $TOTAL"
echo "Concurrency: $CONCURRENCY"
echo "Log dir: $LOG_DIR"
echo "========================"

if [[ "$TOTAL" -eq 0 ]]; then
  echo "No tasks to process. Done."
  exit 0
fi

# Purge leaked CamoFox tabs from previous runs
echo "Cleaning CamoFox tabs..."
for uid in $(curl -sf "$CAMOFOX_URL/health" 2>/dev/null \
  | python3 -c "import sys,json; [print(u) for u in json.load(sys.stdin).get('activeUserIds',[])]" 2>/dev/null); do
  curl -sf -X DELETE "${CAMOFOX_URL}/sessions/${uid}" \
    -H 'Content-Type: application/json' \
    -d "{\"userId\":\"${uid}\"}" >/dev/null 2>&1 || true
done
echo "Done."

mkfifo "$FIFO"
exec 3<>"$FIFO"

for ((i = 0; i < CONCURRENCY; i++)); do
  echo "slot" >&3
done

declare -a PIDS=()

while IFS= read -r task_id; do
  [[ -z "$task_id" ]] && continue

  read -r _ <&3

  (
    run_task "$task_id"
    echo "slot" >&3
  ) &
  PIDS+=($!)

done < "$TASK_SOURCE"

for pid in "${PIDS[@]}"; do
  wait "$pid" 2>/dev/null || true
done

exec 3>&-
rm -f "$FIFO"

echo ""
echo "=== Complete ==="
FINAL_COUNT="$(wc -l < "$LOG_DIR/_completed_tasks.txt" | tr -d ' ')"
echo "Processed $FINAL_COUNT / $TOTAL tasks"
echo "Log dir: $LOG_DIR"

sq "SELECT status, COUNT(*) FROM entries GROUP BY status ORDER BY COUNT(*) DESC;"

rm -f "$WORKSPACE/.resume_tasks.txt" "$WORKSPACE/.active_tasks.txt"
