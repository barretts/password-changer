#!/usr/bin/env python3
"""
Camoufox reachability recheck for blocked/site_dead/failed entries.
Tests each site with Camoufox's anti-detection browser and resets
viable entries to 'pending' for a second agent pass.

Usage: python3 camoufox-recheck.py [--dry-run] [--concurrency N]
"""

import asyncio
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from camoufox.async_api import AsyncCamoufox

WORKSPACE = Path(__file__).parent
DB_PATH = WORKSPACE / "passwords.db"

DRY_RUN = "--dry-run" in sys.argv
CONCURRENCY = 4
for i, arg in enumerate(sys.argv):
    if arg == "--concurrency" and i + 1 < len(sys.argv):
        CONCURRENCY = int(sys.argv[i + 1])

NOT_RETRYABLE_ERRORS = [
    "password",
    "invalid username",
    "invalid email",
    "incorrect password",
    "wrong password",
    "wrong email",
    "account not found",
    "not registered",
    "combo is incorrect",
    "unable to log you in",
    "account locked",
    "account disabled",
    "account has been",
    "no longer exists",
    "email format",
    "not a valid email",
    "email must be",
]


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.row_factory = sqlite3.Row
    return conn


def get_entries():
    conn = get_conn()
    entries = []

    blocked = conn.execute(
        "SELECT id, url, base_url, root_url, domain, error_message FROM entries WHERE status = 'blocked'"
    ).fetchall()
    entries.extend([(dict(r), "blocked") for r in blocked])

    site_dead = conn.execute("""
        SELECT id, url, base_url, root_url, domain, error_message FROM entries 
        WHERE status = 'site_dead'
        AND error_message NOT LIKE '%ERR_NAME_NOT_RESOLVED%'
        AND error_message NOT LIKE '%DNS%resolution%'
        AND error_message NOT LIKE '%parked%'
        AND error_message NOT LIKE '%domain%sale%'
        AND error_message NOT LIKE '%404%'
    """).fetchall()
    entries.extend([(dict(r), "site_dead") for r in site_dead])

    failed = conn.execute(
        "SELECT id, url, base_url, root_url, domain, error_message FROM entries WHERE status = 'failed'"
    ).fetchall()
    for r in failed:
        row = dict(r)
        err = (row.get("error_message") or "").lower()
        if any(phrase in err for phrase in NOT_RETRYABLE_ERRORS):
            continue
        entries.append((row, "failed"))

    conn.close()
    return entries


async def check_url(browser, url: str, timeout: int = 30000) -> dict:
    page = await browser.new_page()
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        await page.wait_for_timeout(3000)

        status = resp.status if resp else 0
        title = await page.title()
        content = await page.content()
        content_len = len(content)

        text = ""
        try:
            text = await page.inner_text("body")
        except Exception:
            pass

        inputs = await page.query_selector_all("input")
        has_form = len(inputs) > 0

        cl = content.lower()
        signals = {
            "has_login_form": has_form and any(
                kw in cl for kw in ["password", "sign in", "log in", "login"]
            ),
            "has_captcha": "captcha" in cl or "recaptcha" in cl or "hcaptcha" in cl,
            "cloudflare_challenge": "cloudflare" in cl and ("ray id" in cl or "just a moment" in title.lower()),
            "access_denied": "access denied" in cl or status == 403,
            "is_blank": content_len < 100,
            "is_error_page": status >= 500,
            "has_content": content_len > 500,
        }

        return {
            "status_code": status,
            "title": title[:100],
            "content_len": content_len,
            "text_preview": text.strip()[:150].replace("\n", " | "),
            "input_count": len(inputs),
            **signals,
        }
    except Exception as e:
        return {"error": str(e), "status_code": 0, "has_content": False}
    finally:
        await page.close()


def classify_result(result: dict, original_status: str) -> str:
    """Returns: 'reset' (retry), 'keep' (don't retry), or 'captcha' (retry with solver)."""
    if result.get("error"):
        return "keep"
    if result.get("has_login_form"):
        return "reset"
    if result.get("has_captcha") and not result.get("cloudflare_challenge"):
        return "captcha"
    if result.get("cloudflare_challenge"):
        return "captcha"
    if result.get("has_content") and not result.get("access_denied"):
        return "reset"
    if result.get("is_blank") or result.get("access_denied"):
        return "keep"
    if result.get("has_content"):
        return "reset"
    return "keep"


async def process_entry(browser, entry: dict, original_status: str, sem: asyncio.Semaphore):
    async with sem:
        entry_id = entry["id"]
        domain = entry.get("domain", "?")
        base_url = entry.get("base_url") or entry.get("url")
        root_url = entry.get("root_url")

        result = await check_url(browser, base_url)

        if result.get("error") and root_url:
            result = await check_url(browser, root_url)

        action = classify_result(result, original_status)

        status_code = result.get("status_code", 0)
        title = result.get("title", "")
        content_len = result.get("content_len", 0)
        err = result.get("error", "")
        preview = result.get("text_preview", "")[:80]
        form = "form" if result.get("has_login_form") else ""
        captcha = "captcha" if result.get("has_captcha") else ""
        tags = " ".join(filter(None, [form, captcha]))

        action_sym = {"reset": "+", "captcha": "~", "keep": "-"}[action]
        print(
            f"  [{action_sym}] {entry_id:>4d} {domain:40s} "
            f"HTTP {status_code:3d} {content_len:>7,}b {tags:12s} "
            f"{title[:40] or err[:40]}"
        )

        return entry_id, action, original_status


async def main():
    entries = get_entries()
    print(f"Entries to recheck: {len(entries)}")
    print(f"  blocked:   {sum(1 for _, s in entries if s == 'blocked')}")
    print(f"  site_dead: {sum(1 for _, s in entries if s == 'site_dead')}")
    print(f"  failed:    {sum(1 for _, s in entries if s == 'failed')}")
    print(f"Concurrency: {CONCURRENCY}")
    print(f"Dry run: {DRY_RUN}")
    print()

    if not entries:
        print("Nothing to recheck.")
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    reset_ids = []
    captcha_ids = []
    keep_ids = []

    async with AsyncCamoufox(headless=True, humanize=True) as browser:
        tasks = [
            process_entry(browser, entry, orig_status, sem)
            for entry, orig_status in entries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            print(f"  [!] Exception: {r}")
            continue
        entry_id, action, _ = r
        if action == "reset":
            reset_ids.append(entry_id)
        elif action == "captcha":
            captcha_ids.append(entry_id)
        else:
            keep_ids.append(entry_id)

    all_reset = reset_ids + captcha_ids
    print(f"\n{'='*60}")
    print(f"RESULTS:")
    print(f"  Reset to pending (site loads):  {len(reset_ids)}")
    print(f"  Reset to pending (has CAPTCHA): {len(captcha_ids)}")
    print(f"  Keep current status:            {len(keep_ids)}")
    print(f"  TOTAL to retry:                 {len(all_reset)}")

    if DRY_RUN:
        print("\nDry run -- no DB changes made.")
        return

    if not all_reset:
        print("\nNo entries to reset.")
        return

    conn = get_conn()
    for entry_id in all_reset:
        conn.execute(
            "UPDATE entries SET status = 'pending', attempt_count = 0, error_message = 'Camoufox recheck: site reachable', updated_at = datetime('now') WHERE id = ?",
            (entry_id,),
        )
    conn.commit()

    cursor = conn.execute(
        "SELECT id FROM entries WHERE status = 'pending' ORDER BY priority, id"
    )
    task_ids = [str(r[0]) for r in cursor.fetchall()]
    (WORKSPACE / "tasks.txt").write_text("\n".join(task_ids) + "\n")

    conn.close()

    print(f"\nReset {len(all_reset)} entries to pending.")
    print(f"Regenerated tasks.txt: {len(task_ids)} pending tasks.")
    print(f"\nRun the second pass:")
    print(f"  ./run-password-changer.sh 4")


if __name__ == "__main__":
    asyncio.run(main())
