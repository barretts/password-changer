#!/usr/bin/env python3
"""Apply skip-domains.conf to passwords.db, marking matching pending entries as skipped."""

import sqlite3
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent
DB_PATH = WORKSPACE / "passwords.db"
SKIP_CONF = WORKSPACE / "skip-domains.conf"
TASK_FILE = WORKSPACE / "tasks.txt"


def load_skip_rules(conf_path: Path) -> list[tuple[str, str, str]]:
    rules = []
    for line in conf_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            pattern, reason, category = parts[0], parts[1], parts[2]
            rules.append((pattern, reason, category))
    return rules


def main():
    dry_run = "--dry-run" in sys.argv

    if not SKIP_CONF.exists():
        print(f"Skip config not found: {SKIP_CONF}", file=sys.stderr)
        sys.exit(1)

    rules = load_skip_rules(SKIP_CONF)
    print(f"Loaded {len(rules)} skip rules")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")

    pending = conn.execute(
        "SELECT id, domain, url FROM entries WHERE status = 'pending'"
    ).fetchall()

    skip_count = 0
    for entry_id, domain, url in pending:
        for pattern, reason, category in rules:
            if pattern in (domain or "") or pattern in (url or ""):
                if dry_run:
                    print(f"  [DRY] Would skip {entry_id} ({domain}): {reason}")
                else:
                    conn.execute(
                        "UPDATE entries SET status = 'skipped', agent_notes = ?, site_category = ?, updated_at = datetime('now') WHERE id = ?",
                        (f"Auto-skipped: {reason}", category, entry_id),
                    )
                skip_count += 1
                break

    if not dry_run:
        conn.commit()

        cursor = conn.execute(
            "SELECT id FROM entries WHERE status = 'pending' ORDER BY priority, id"
        )
        task_ids = [str(r[0]) for r in cursor.fetchall()]
        TASK_FILE.write_text("\n".join(task_ids) + "\n")
        print(f"Regenerated tasks.txt: {len(task_ids)} pending tasks")

    conn.close()
    print(f"{'Would skip' if dry_run else 'Skipped'}: {skip_count} entries")


if __name__ == "__main__":
    main()
