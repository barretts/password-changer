#!/usr/bin/env python3
"""Parse LastPass CSV export, create SQLite database, generate task manifest."""

import csv
import ipaddress
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import tldextract
except ImportError:
    print("Installing tldextract...", file=sys.stderr)
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tldextract"])
    import tldextract

WORKSPACE = Path(__file__).parent
CSV_PATH = WORKSPACE / "lastpass_vault_export.csv"
DB_PATH = WORKSPACE / "passwords.db"
TASK_FILE = WORKSPACE / "tasks.txt"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    base_url TEXT,
    root_url TEXT,
    domain TEXT,
    username TEXT,
    old_password TEXT,
    new_password TEXT,
    totp TEXT,
    extra TEXT,
    name TEXT,
    grouping_label TEXT,
    status TEXT DEFAULT 'pending',
    attempt_count INTEGER DEFAULT 0,
    last_attempt_at TEXT,
    error_message TEXT,
    site_category TEXT,
    password_difficulty TEXT,
    mfa_type TEXT,
    site_status TEXT,
    password_requirements TEXT,
    agent_notes TEXT,
    priority INTEGER DEFAULT 999,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_entries_status ON entries(status);
CREATE INDEX IF NOT EXISTS idx_entries_domain ON entries(domain);
CREATE INDEX IF NOT EXISTS idx_entries_priority ON entries(priority);
"""

HIGH_VALUE_DOMAINS = {
    "gmail.com", "outlook.com", "yahoo.com", "protonmail.com", "icloud.com",
    "chase.com", "bankofamerica.com", "wellsfargo.com", "paypal.com", "venmo.com",
    "aws.amazon.com", "console.cloud.google.com", "portal.azure.com",
    "github.com", "gitlab.com", "bitbucket.org",
    "amazon.com", "apple.com", "google.com", "microsoft.com",
}


def compute_base_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.hostname:
            return f"{parsed.scheme}://{parsed.hostname}/"
        return None
    except Exception:
        return None


def compute_root_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if not parsed.hostname:
            return None
        ext = tldextract.extract(parsed.hostname)
        if ext.domain and ext.suffix:
            registrable = f"{ext.domain}.{ext.suffix}"
            if registrable != parsed.hostname:
                scheme = parsed.scheme or "https"
                return f"{scheme}://{registrable}/"
        return None
    except Exception:
        return None


def is_raw_ip(domain: str | None) -> bool:
    if not domain:
        return False
    try:
        ipaddress.ip_address(domain)
        return True
    except ValueError:
        return False


def has_valid_host(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return bool(parsed.hostname and "." in parsed.hostname)
    except Exception:
        return False


def is_actionable(row: dict) -> bool:
    url = row.get("url", "")
    username = row.get("username", "")
    password = row.get("password", "")

    if url.startswith("http://sn"):
        return False
    if not username or not password:
        return False

    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if not has_valid_host(url):
        return False
    if is_raw_ip(hostname):
        return False

    private_prefixes = ("192.168.", "10.", "127.", "172.16.", "172.17.", "172.18.",
                        "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                        "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                        "172.29.", "172.30.", "172.31.", "0.", "169.254.")
    if any(hostname.startswith(p) for p in private_prefixes):
        return False
    if hostname in ("localhost", ""):
        return False

    return True


def get_priority(domain: str, password: str, password_counts: dict) -> int:
    if any(hv in (domain or "") for hv in HIGH_VALUE_DOMAINS):
        return 1
    if password_counts.get(password, 0) > 3:
        return 2
    return 999


def main():
    if not CSV_PATH.exists():
        print(f"CSV not found: {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if is_actionable(row):
                rows.append(row)

    print(f"Actionable entries: {len(rows)}")

    password_counts: dict[str, int] = {}
    for row in rows:
        pw = row.get("password", "")
        password_counts[pw] = password_counts.get(pw, 0) + 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.executescript(SCHEMA)

    for row in rows:
        url = row.get("url", "")
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        base_url = compute_base_url(url)
        root_url = compute_root_url(url)
        password = row.get("password", "")
        priority = get_priority(domain, password, password_counts)

        conn.execute(
            """INSERT INTO entries
               (url, base_url, root_url, domain, username, old_password, totp, extra, name, grouping_label, priority)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                url,
                base_url,
                root_url,
                domain,
                row.get("username", ""),
                password,
                row.get("totp", ""),
                row.get("extra", ""),
                row.get("name", ""),
                row.get("grouping", ""),
                priority,
            ),
        )

    conn.commit()

    cursor = conn.execute(
        "SELECT id FROM entries WHERE status = 'pending' ORDER BY priority, id"
    )
    task_ids = [str(r[0]) for r in cursor.fetchall()]

    TASK_FILE.write_text("\n".join(task_ids) + "\n")
    print(f"Task manifest: {len(task_ids)} tasks written to {TASK_FILE}")

    conn.close()


if __name__ == "__main__":
    main()
