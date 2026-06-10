#!/usr/bin/env python3
"""
get_gsccca_cookie.py — LOCAL USE ONLY. Never commit this file's output.

Opens a visible Chromium window via Playwright so you can log into GSCCCA
manually. After login, captures ASP.NET_SessionId cookies from both domains
and writes them to cookies.json (gitignored).

Usage:
    python get_gsccca_cookie.py

Output:
    cookies.json  — JSON array consumed by the main scraper as GSCCCA_COOKIES
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("ERROR: Playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

LOGIN_URL = "https://www.gsccca.org/Login.aspx"
SEARCH_URL = "https://search.gsccca.org/RealEstate/InstrumentTypeSearch.aspx"
OUTPUT_FILE = Path(__file__).parent / "cookies.json"

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          GSCCCA Cookie Capture — Manual Login Required       ║
╠══════════════════════════════════════════════════════════════╣
║  1. A Chromium window will open to the GSCCCA login page.   ║
║  2. Log in with your GSCCCA account credentials.            ║
║  3. Once the search page loads, return here and press ENTER. ║
║  4. Your session cookies will be saved to cookies.json.     ║
╚══════════════════════════════════════════════════════════════╝
"""


def capture_cookies() -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        print(BANNER)
        print(f"Opening: {LOGIN_URL}")
        page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)

        input("\nPress ENTER after you have logged in and the search page is visible... ")

        # Navigate to search domain so its cookie is set
        try:
            page.goto(SEARCH_URL, wait_until="networkidle", timeout=20000)
        except PlaywrightTimeout:
            print("Warning: search page timed out loading — cookies may still be valid.")

        all_cookies = context.cookies()
        browser.close()

        # Keep only ASP.NET session cookies from both domains
        session_cookies = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c["path"],
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            for c in all_cookies
            if "ASP.NET_SessionId" in c["name"] or "ASPXAUTH" in c["name"]
        ]

        if not session_cookies:
            # Fallback: grab everything from gsccca domains
            session_cookies = [
                {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c["path"],
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
                for c in all_cookies
                if "gsccca" in c["domain"]
            ]

        return session_cookies


def main():
    cookies = capture_cookies()

    if not cookies:
        print("\nERROR: No GSCCCA cookies found. Did you complete login?")
        sys.exit(1)

    OUTPUT_FILE.write_text(json.dumps(cookies, indent=2))

    print(f"\n✓ Captured {len(cookies)} cookie(s):")
    for c in cookies:
        print(f"  [{c['domain']}] {c['name']} = {c['value'][:20]}...")

    print(f"\n✓ Saved to: {OUTPUT_FILE}")
    print("\nNext step: copy the contents of cookies.json into GitHub Secret GSCCCA_COOKIES")
    print("           (Settings → Secrets → Actions → New repository secret)")
    print("\nCookie expires in ~12 hours. Re-run this script before each scrape run if needed.")


if __name__ == "__main__":
    main()
