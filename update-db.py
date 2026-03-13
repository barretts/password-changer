#!/usr/bin/env python3
"""Extract JSON result from agent log and update passwords.db."""

import json
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "passwords.db"


def extract_json_from_output(text: str) -> dict | None:
    lines = text.strip().splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if "task_id" in obj and "status" in obj:
                return obj
        except json.JSONDecodeError:
            continue

    # Slow path: scan backwards for any { ... } that parses and has task_id
    pos = len(text)
    while True:
        close = text.rfind("}", 0, pos)
        if close == -1:
            break
        depth = 0
        start = None
        for i in range(close, -1, -1):
            ch = text[i]
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
            if depth == 0:
                start = i
                break
        if start is not None:
            candidate = text[start : close + 1]
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict) and "task_id" in obj and "status" in obj:
                    pipe_vals = "success|failed|mfa_required|site_dead|blocked|skipped"
                    if obj.get("status") != pipe_vals:
                        return obj
            except json.JSONDecodeError:
                pass
        pos = close - 1 if close > 0 else -1
        if pos < 0:
            break

    return None


def update_db(task_id: int, result: dict):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")

    fields = {
        "status": result.get("status", "failed"),
        "new_password": result.get("new_password"),
        "site_category": result.get("site_category"),
        "password_difficulty": result.get("password_difficulty"),
        "mfa_type": result.get("mfa_type"),
        "site_status": result.get("site_status"),
        "password_requirements": result.get("password_requirements"),
        "agent_notes": result.get("agent_notes"),
        "error_message": result.get("error_message"),
        "updated_at": "datetime('now')",
    }

    set_clause = ", ".join(
        f"{k} = datetime('now')" if v == "datetime('now')" else f"{k} = ?"
        for k, v in fields.items()
    )
    values = [v for v in fields.values() if v != "datetime('now')"]
    values.append(task_id)

    conn.execute(f"UPDATE entries SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <task_id> <log_file>", file=sys.stderr)
        sys.exit(1)

    task_id = int(sys.argv[1])
    log_file = Path(sys.argv[2])

    if not log_file.exists():
        print(f"Log file not found: {log_file}", file=sys.stderr)
        update_db(task_id, {"status": "failed", "error_message": "No log file produced"})
        return

    text = log_file.read_text(errors="replace")
    result = extract_json_from_output(text)

    if result is None:
        print(f"Task {task_id}: No valid JSON found in output", file=sys.stderr)
        update_db(task_id, {"status": "failed", "error_message": "Agent produced no parseable JSON result"})
        return

    print(f"Task {task_id}: {result.get('status')}")
    update_db(task_id, result)


if __name__ == "__main__":
    main()
