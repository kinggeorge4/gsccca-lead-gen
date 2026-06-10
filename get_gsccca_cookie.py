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

# The search system (search.gsccca.org) uses a separate classic-ASP login at
# /login.asp, distinct from www.gsccca.org. The Premium Instrument Type Search
# lives at /RealEstatePremium/ and redirects unauthenticated users to login.asp.
LOGIN_URL = "https://search.gsccca.org/RealEstatePremium/InstrumentTypeSearch.aspx"
SEARCH_URL = "https://search.gsccca.org/RealEstatePremium/InstrumentTypeSearch.aspx"
OUTPUT_FILE = Path(__file__).parent / "cookies.json"

# Cookies we must have for the scraper to work
REQUIRED_COOKIES = {"GUID", "ASPSESSIONID"}  # ASPSESSIONID prefix match

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          GSCCCA Cookie Capture — Manual Login Required       ║
╠══════════════════════════════════════════════════════════════╣
║  1. A Chromium window will open to the GSCCCA search login. ║
║  2. Log in with your GSCCCA Premium account credentials.    ║
║  3. Wait until the Instrument Type Search page fully loads. ║
║  4. Return here and press ENTER.                            ║
║  5. Your session cookies will be saved to cookies.json.     ║
╚══════════════════════════════════════════════════════════════╝
"""


def _keep_cookie(c: dict) -> bool:
    name = c["name"]
    domain = c.get("domain", "")
    if "gsccca" not in domain:
        return False
    # Always keep GUID (cross-domain auth token) and ASPSESSIONID (search session)
    if name == "GUID" or name.startswith("ASPSESSIONID"):
        return True
    # Keep any other non-analytics cookies from gsccca domains
    if name.startswith("_ga") or name.startswith("_gid"):
        return False
    return True


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
        # This will redirect to login.asp automatically if not authenticated
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

        input(
            "\nPress ENTER after you have logged in and the "
            "Instrument Type Search page is fully visible... "
        )

        # Confirm we landed on the search page (not still on login)
        current_url = page.url
        if "login" in current_url.lower():
            print("Warning: still on login page — did login complete?")

        all_cookies = context.cookies()
        browser.close()

        kept = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c["path"],
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            for c in all_cookies
            if _keep_cookie(c)
        ]

        return kept


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
