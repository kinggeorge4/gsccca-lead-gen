#!/usr/bin/env python3
"""
refresh_cookies.py — Headless automated GSCCCA login and cookie capture.

Runs on the Digital Ocean droplet (self-hosted runner). Logs in with
GSCCCA_USERNAME / GSCCCA_PASSWORD env vars, extracts session cookies,
and saves them to /opt/gsccca/cookies.json for use by the main scraper.

Usage (via GitHub Actions):
    python refresh_cookies.py

Usage (manual test on droplet):
    GSCCCA_USERNAME=geo4th GSCCCA_PASSWORD=... python refresh_cookies.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

COOKIE_PATH = Path("/opt/gsccca/cookies.json")
LOGIN_URL   = "https://search.gsccca.org/RealEstatePremium/InstrumentTypeSearch.aspx"


def _keep_cookie(c: dict) -> bool:
    domain = c.get("domain", "")
    name   = c["name"]
    if "gsccca" not in domain:
        return False
    if name.startswith("_ga") or name.startswith("_gid"):
        return False
    return True


def refresh() -> list[dict]:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

    username = os.environ.get("GSCCCA_USERNAME", "")
    password = os.environ.get("GSCCCA_PASSWORD", "")
    if not username or not password:
        logger.error("GSCCCA_USERNAME / GSCCCA_PASSWORD env vars not set")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        logger.info("Navigating to GSCCCA login...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

        # Fill login form (redirected to login.asp automatically)
        try:
            page.wait_for_selector("input[name='txtUserID'], input[name='txtUsername']", timeout=10000)
        except PlaywrightTimeout:
            logger.error("Login form not found — page: %s", page.url)
            browser.close()
            sys.exit(1)

        # Try both possible field name variants
        for field in ("txtUserID", "txtUsername", "username"):
            if page.query_selector(f"input[name='{field}']"):
                page.fill(f"input[name='{field}']", username)
                break

        for field in ("txtPassword", "password"):
            if page.query_selector(f"input[name='{field}']"):
                page.fill(f"input[name='{field}']", password)
                break

        logger.info("Submitting login form...")
        page.keyboard.press("Enter")

        try:
            page.wait_for_url("**/RealEstatePremium/**", timeout=20000)
        except PlaywrightTimeout:
            # Some redirects go through intermediate pages — just wait for load
            page.wait_for_load_state("networkidle", timeout=20000)

        if "login" in page.url.lower():
            logger.error("Still on login page after submit — check credentials")
            browser.close()
            sys.exit(1)

        logger.info("Login successful. Current URL: %s", page.url)

        all_cookies = context.cookies()
        browser.close()

    kept = [
        {
            "name":        c["name"],
            "value":       c["value"],
            "domain":      c["domain"],
            "path":        c["path"],
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        for c in all_cookies
        if _keep_cookie(c)
    ]

    if not kept:
        logger.error("No GSCCCA cookies captured — login may have failed silently")
        sys.exit(1)

    return kept


def main():
    cookies = refresh()

    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_PATH.write_text(json.dumps(cookies, indent=2))

    logger.info("Saved %d cookies to %s", len(cookies), COOKIE_PATH)
    for c in cookies:
        logger.info("  [%s] %s", c["domain"], c["name"])


if __name__ == "__main__":
    main()
