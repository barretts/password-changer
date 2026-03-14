#!/usr/bin/env python3
"""
Standalone Camoufox test: hit a known-blocked domain and see if it loads.
Usage: python3 test-camoufox.py [url ...]
Default tests several previously-blocked domains.
"""

import sys
import asyncio
from camoufox.async_api import AsyncCamoufox


BLOCKED_DOMAINS = [
    "https://login.xfinity.com",        # Akamai WAF
    "https://signin.ebay.com",           # reCAPTCHA
    "https://www.meetup.com/login",      # CAPTCHA
    "https://www.reddit.com/login",      # Bot detection
]


async def test_url(browser, url: str):
    print(f"\n{'='*60}")
    print(f"Testing: {url}")
    print(f"{'='*60}")

    page = await browser.new_page()
    try:
        resp = await page.goto(url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(3000)

        status = resp.status if resp else "no response"
        final_url = page.url
        title = await page.title()
        content = await page.content()
        content_len = len(content)

        text = await page.inner_text("body")
        text_preview = text.strip()[:200].replace("\n", " | ")

        blocked_signals = {
            "access denied": "access denied" in content.lower(),
            "blocked in title": "blocked" in title.lower(),
            "captcha": "captcha" in content.lower() or "recaptcha" in content.lower(),
            "challenge-form": "challenge" in content.lower() and "form" in content.lower(),
            "cloudflare ray": "cloudflare" in content.lower() and "ray id" in content.lower(),
            "just a moment": "just a moment" in title.lower(),
            "403 status": "403" in str(status),
            "akamai": "akamai" in content.lower() or "edgesuite" in content.lower(),
        }

        triggered = [k for k, v in blocked_signals.items() if v]
        is_blocked = len(triggered) > 0

        print(f"  Status:    {status}")
        print(f"  Title:     {title[:80]}")
        print(f"  Final URL: {final_url}")
        print(f"  HTML size: {content_len:,} bytes")
        print(f"  Preview:   {text_preview[:120]}")
        print(f"  Blocked:   {'YES -- ' + ', '.join(triggered) if is_blocked else 'NO'}")

        return not is_blocked

    except Exception as e:
        print(f"  ERROR:     {type(e).__name__}: {e}")
        return False
    finally:
        await page.close()


async def main():
    urls = sys.argv[1:] if len(sys.argv) > 1 else BLOCKED_DOMAINS

    print("Starting Camoufox (headless, humanize=True)...")
    async with AsyncCamoufox(headless=True, humanize=True) as browser:
        results = {}
        for url in urls:
            ok = await test_url(browser, url)
            results[url] = ok

    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    for url, ok in results.items():
        tag = "\033[32mPASS\033[0m" if ok else "\033[31mBLOCKED\033[0m"
        print(f"  {tag:17s}  {url}")

    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{len(results)} passed")


if __name__ == "__main__":
    asyncio.run(main())
