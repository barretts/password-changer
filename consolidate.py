#!/usr/bin/env python3
"""Aggregate results from passwords.db into summary report and CSV exports."""

import csv
import sqlite3
from pathlib import Path

WORKSPACE = Path(__file__).parent
DB_PATH = WORKSPACE / "passwords.db"
LP_CSV_PATH = WORKSPACE / "lastpass_vault_export.csv"


def _combine_notes(agent_notes: str | None, extra: str | None, pw_reqs: str | None) -> str:
    """Merge agent observations, original LastPass notes, and password requirements."""
    parts = []
    if agent_notes and agent_notes.strip():
        parts.append(agent_notes.strip())
    if pw_reqs and pw_reqs.strip():
        parts.append(f"Password requirements: {pw_reqs.strip()}")
    if extra and extra.strip():
        parts.append(f"LastPass note:\n{extra.strip()}")
    return "\n\n".join(parts)


def _load_lastpass_secure_notes() -> list[dict]:
    """Read Secure Notes (url == 'http://sn') from the LastPass vault export."""
    if not LP_CSV_PATH.exists():
        return []
    notes = []
    with LP_CSV_PATH.open(newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if (row.get("url") or "").strip() != "http://sn":
                continue
            extra = (row.get("extra") or "").strip()
            if not extra:
                continue
            notes.append({
                "name": (row.get("name") or "").strip(),
                "folder": (row.get("grouping") or "").strip(),
                "notes": extra,
            })
    return notes


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.row_factory = sqlite3.Row

    # Status breakdown
    status_counts = dict(
        conn.execute("SELECT status, COUNT(*) FROM entries GROUP BY status ORDER BY COUNT(*) DESC").fetchall()
    )

    # Category breakdown
    category_counts = dict(
        conn.execute(
            "SELECT site_category, COUNT(*) FROM entries WHERE site_category IS NOT NULL GROUP BY site_category ORDER BY COUNT(*) DESC"
        ).fetchall()
    )

    # Difficulty breakdown
    difficulty_counts = dict(
        conn.execute(
            "SELECT password_difficulty, COUNT(*) FROM entries WHERE password_difficulty IS NOT NULL GROUP BY password_difficulty ORDER BY COUNT(*) DESC"
        ).fetchall()
    )

    # MFA breakdown
    mfa_counts = dict(
        conn.execute(
            "SELECT mfa_type, COUNT(*) FROM entries WHERE mfa_type IS NOT NULL GROUP BY mfa_type ORDER BY COUNT(*) DESC"
        ).fetchall()
    )

    total = sum(status_counts.values())

    report_lines = [
        "# Password Changer Report",
        "",
        f"**Total entries:** {total}",
        "",
        "## Status Breakdown",
        "",
        "| Status | Count | % |",
        "|--------|-------|---|",
    ]
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        report_lines.append(f"| {status} | {count} | {pct:.1f}% |")

    if category_counts:
        report_lines += ["", "## Site Categories", "", "| Category | Count |", "|----------|-------|"]
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            report_lines.append(f"| {cat} | {count} |")

    if difficulty_counts:
        report_lines += ["", "## Password Difficulty", "", "| Difficulty | Count |", "|------------|-------|"]
        for diff, count in sorted(difficulty_counts.items(), key=lambda x: -x[1]):
            report_lines.append(f"| {diff} | {count} |")

    if mfa_counts:
        report_lines += ["", "## MFA Types", "", "| MFA Type | Count |", "|----------|-------|"]
        for mfa, count in sorted(mfa_counts.items(), key=lambda x: -x[1]):
            report_lines.append(f"| {mfa} | {count} |")

    report_path = WORKSPACE / "REPORT.md"
    report_path.write_text("\n".join(report_lines) + "\n")
    print(f"Report: {report_path}")

    # New credentials CSV (successful changes)
    successes = conn.execute(
        "SELECT url, domain, username, new_password, old_password, site_category, password_requirements FROM entries WHERE status = 'success' ORDER BY domain"
    ).fetchall()

    creds_path = WORKSPACE / "new-credentials.csv"
    with open(creds_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["url", "domain", "username", "new_password", "old_password", "site_category", "password_requirements"])
        for row in successes:
            writer.writerow(list(row))
    print(f"New credentials: {len(successes)} entries -> {creds_path}")

    # Manual queue (needs human intervention)
    manual = conn.execute(
        "SELECT id, url, domain, username, status, mfa_type, error_message, agent_notes FROM entries WHERE status IN ('mfa_required', 'blocked', 'failed') ORDER BY status, domain"
    ).fetchall()

    manual_path = WORKSPACE / "manual-queue.csv"
    with open(manual_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "url", "domain", "username", "status", "mfa_type", "error_message", "agent_notes"])
        for row in manual:
            writer.writerow(list(row))
    print(f"Manual queue: {len(manual)} entries -> {manual_path}")

    # Bitwarden-format import CSV (successful changes + LastPass Secure Notes)
    bw_rows = conn.execute(
        "SELECT url, domain, username, new_password, totp, grouping_label, agent_notes, extra, password_requirements"
        " FROM entries WHERE status = 'success' ORDER BY domain"
    ).fetchall()

    secure_notes = _load_lastpass_secure_notes()

    bw_path = WORKSPACE / "export-bitwarden.csv"
    bw_fields = ["folder", "favorite", "type", "name", "notes", "fields", "reprompt",
                 "login_uri", "login_username", "login_password", "login_totp"]
    with open(bw_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=bw_fields)
        writer.writeheader()
        for row in bw_rows:
            notes = _combine_notes(row["agent_notes"], row["extra"], row["password_requirements"])
            writer.writerow({
                "folder": row["grouping_label"] or "",
                "favorite": "",
                "type": "login",
                "name": row["domain"] or "",
                "notes": notes,
                "fields": "",
                "reprompt": 0,
                "login_uri": row["url"] or "",
                "login_username": row["username"] or "",
                "login_password": row["new_password"] or "",
                "login_totp": row["totp"] or "",
            })
        for sn in secure_notes:
            writer.writerow({
                "folder": sn["folder"],
                "favorite": "",
                "type": "note",
                "name": sn["name"],
                "notes": sn["notes"],
                "fields": "",
                "reprompt": 0,
                "login_uri": "",
                "login_username": "",
                "login_password": "",
                "login_totp": "",
            })
    print(f"Bitwarden export: {len(bw_rows)} logins + {len(secure_notes)} secure notes -> {bw_path}")

    # 1Password-format import CSV (successful changes + LastPass Secure Notes)
    op_path = WORKSPACE / "export-1password.csv"
    op_fields = ["Title", "URL", "Username", "Password", "Notes", "Tags"]
    with open(op_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=op_fields)
        writer.writeheader()
        for row in bw_rows:
            notes = _combine_notes(row["agent_notes"], row["extra"], row["password_requirements"])
            writer.writerow({
                "Title": row["domain"] or "",
                "URL": row["url"] or "",
                "Username": row["username"] or "",
                "Password": row["new_password"] or "",
                "Notes": notes,
                "Tags": row["grouping_label"] or "",
            })
        for sn in secure_notes:
            writer.writerow({
                "Title": sn["name"],
                "URL": "",
                "Username": "",
                "Password": "",
                "Notes": sn["notes"],
                "Tags": sn["folder"],
            })
    print(f"1Password export: {len(bw_rows)} logins + {len(secure_notes)} secure notes -> {op_path}")

    conn.close()


if __name__ == "__main__":
    main()
