"""
fetch.py — Core GSCCCA scraper (Playwright-based).

Raw HTTP POST silently fails — the server performs browser-specific checks
during form submission. Playwright with injected session cookies works correctly.

Cookie capture is done locally with get_gsccca_cookie.py (never in CI).
This module NEVER attempts login; it only injects pre-captured cookies.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from .counties import COUNTY_IDS, GA_COUNTIES, resolve_counties
from .instruments import ALL_INSTRUMENTS, TIER_1, TIER_2, get_tier
from .score import score_lead

logger = logging.getLogger(__name__)

BASE_RE_URL    = "https://search.gsccca.org/RealEstatePremium/InstrumentTypeSearch.aspx"
RESULTS_RE_URL = "https://search.gsccca.org/RealEstatePremium/InstrumentTypeSearchResults.aspx"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

COOKIE_MAX_AGE_HOURS = 12
MAX_DATE_RANGE_DAYS  = 30  # GSCCCA client-side JS enforces ≤30-day ranges


# ─── Cookie loading ────────────────────────────────────────────────────────────

def load_cookies(source: str | None = None) -> list[dict]:
    """
    Load cookies from:
      1. source argument (JSON string or file path)
      2. GSCCCA_COOKIES env var (JSON string)
      3. cookies.json in repo root
    """
    raw = source or os.environ.get("GSCCCA_COOKIES") or ""

    if not raw:
        cookie_file = Path(__file__).parents[1] / "cookies.json"
        if cookie_file.exists():
            raw = cookie_file.read_text()
        else:
            raise FileNotFoundError(
                "No cookies found. Run get_gsccca_cookie.py first, "
                "or set GSCCCA_COOKIES env var."
            )

    if raw.strip().startswith(("[", "{")):
        cookies = json.loads(raw)
    else:
        cookies = json.loads(Path(raw).read_text())

    _check_cookie_age(cookies)
    return cookies if isinstance(cookies, list) else [cookies]


def _check_cookie_age(cookies: list[dict]) -> None:
    for c in cookies:
        captured_at = c.get("captured_at")
        if not captured_at:
            continue
        age = datetime.now(timezone.utc) - datetime.fromisoformat(captured_at)
        if age > timedelta(hours=COOKIE_MAX_AGE_HOURS):
            logger.warning(
                "Cookie captured %.1f hours ago (max %d h). "
                "Re-run get_gsccca_cookie.py to refresh.",
                age.total_seconds() / 3600,
                COOKIE_MAX_AGE_HOURS,
            )


def _to_playwright_cookies(cookies: list[dict]) -> list[dict]:
    """Convert scraper cookie dicts to Playwright add_cookies format (domain+path)."""
    return [
        {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": "/"}
        for c in cookies
    ]


# ─── HTML parsing ─────────────────────────────────────────────────────────────

def _parse_dashboard_html(
    html: str, county: str, instrument: str, source_url: str
) -> tuple[list[dict], int]:
    """
    Parse one page of Dashboard-mode results.

    Span ID patterns (N = record index, M = multi-value index):
      BodyContent_lvDashboard_lblBook_N
      BodyContent_lvDashboard_lblPage_N
      BodyContent_lvDashboard_lblDateFiled_N
      BodyContent_lvDashboard_lvExpandedGrantor_N_lblGrantorName_M
      BodyContent_lvDashboard_lvExpandedGrantee_N_lblGranteeName_M
      BodyContent_lvDashboard_lvCrossReference_N_lblXRefType_M  (PT-61 sale price)

    Returns (records_on_this_page, total_record_count).
    """
    soup = BeautifulSoup(html, "lxml")

    total = 0
    found_span = soup.find("span", id="BodyContent_lvDashboard_lblDashboardNumberFound")
    if found_span:
        m = re.search(r"(\d[\d,]*)", found_span.get_text())
        if m:
            total = int(m.group(1).replace(",", ""))

    if total == 0:
        return [], 0

    tier = get_tier(instrument)
    records: list[dict] = []

    for n in range(total):
        book_span = soup.find("span", id=f"BodyContent_lvDashboard_lblBook_{n}")
        if not book_span:
            break

        page_span = soup.find("span", id=f"BodyContent_lvDashboard_lblPage_{n}")
        date_span = soup.find("span", id=f"BodyContent_lvDashboard_lblDateFiled_{n}")

        book = book_span.get_text(strip=True)
        page = page_span.get_text(strip=True) if page_span else ""
        book_page = f"Book {book} Page {page}" if book and page else book
        file_date = date_span.get_text(strip=True) if date_span else ""

        grantors = _collect_names(
            soup, f"BodyContent_lvDashboard_lvExpandedGrantor_{n}_lblGrantorName"
        )
        grantees = _collect_names(
            soup, f"BodyContent_lvDashboard_lvExpandedGrantee_{n}_lblGranteeName"
        )
        consideration = _extract_pt61_price(soup, n)

        grantor = "; ".join(grantors)
        grantee = "; ".join(grantees)

        if not grantor and not grantee:
            continue

        records.append({
            "grantor_name":         grantor,
            "grantee_name":         grantee,
            "instrument_type":      instrument,
            "book_page":            book_page,
            "file_date":            file_date,
            "county":               county,
            "parcel_id":            "",
            "consideration_amount": consideration,
            "tier":                 tier or 0,
            "scraped_at":           datetime.now(timezone.utc).isoformat(),
            "source_url":           source_url,
        })

    return records, total


def _collect_names(soup: BeautifulSoup, id_prefix: str) -> list[str]:
    names = []
    for m in range(30):
        s = soup.find("span", id=f"{id_prefix}_{m}")
        if not s:
            break
        name = s.get_text(strip=True)
        if name:
            names.append(name)
    return names


def _extract_pt61_price(soup: BeautifulSoup, record_idx: int) -> str:
    """Extract sale price from PT-61 cross-reference entry, e.g. 'Sale Price: $275,000.00'."""
    for m in range(20):
        span = soup.find(
            "span",
            id=f"BodyContent_lvDashboard_lvCrossReference_{record_idx}_lblXRefType_{m}",
        )
        if not span:
            break
        text = span.get_text(strip=True)
        price_match = re.search(r"Sale Price:\s*(\$[\d,]+(?:\.\d+)?)", text, re.I)
        if price_match:
            return price_match.group(1)
    return ""


def _parse_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    for span_id in (
        "BodyContent_lvDashboard_lblDashboardCurrentPageTop",
        "BodyContent_lvDashboard_lblDashboardCurrentPageBottom",
    ):
        s = soup.find("span", id=span_id)
        if s:
            m = re.search(r"Page\s+\d+\s+of\s+(\d+)", s.get_text(), re.I)
            if m:
                return int(m.group(1))
    return 1


def _check_session_expired(html: str) -> bool:
    lower = html.lower()
    return (
        "gsccca.org - login" in lower
        or "txtpassword" in lower
        or ("login" in lower and "txtuserid" in lower)
    )


# ─── Search via Playwright ────────────────────────────────────────────────────

def search_instrument(
    page,
    county: str,
    instrument_name: str,
    instrument_id: str,
    date_from: str,
    date_to: str,
) -> list[dict]:
    """
    Fetch all records for one county × instrument combination.

    page is a live Playwright Page with session cookies already injected.
    Returns a flat list of record dicts across all result pages.
    """
    county_id = COUNTY_IDS.get(county.upper())
    if not county_id:
        logger.warning("Unknown county: %s — skipping", county)
        return []

    try:
        page.goto(BASE_RE_URL, wait_until="domcontentloaded", timeout=20000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading search form for %s / %s", county, instrument_name)
        return []

    if _check_session_expired(page.content()):
        raise RuntimeError("GSCCCA session expired — re-run get_gsccca_cookie.py")

    page.select_option("select[name='ctl00$BodyContent$ddlCounties']",       value=county_id)
    page.select_option("select[name='ctl00$BodyContent$ddlInstrumentTypes']", value=instrument_id)
    page.fill("input[name='ctl00$BodyContent$txtDateFrom']",                  date_from)
    page.fill("input[name='ctl00$BodyContent$txtDateTo']",                    date_to)
    page.select_option("select[name='ctl00$BodyContent$ddlRecordsPerPage']",  value="100")
    page.select_option("select[name='ctl00$BodyContent$ddlDisplayType']",     value="1")  # Dashboard

    page.click("input[value='Begin Search']")
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeout:
        page.wait_for_load_state("domcontentloaded", timeout=10000)

    if _check_session_expired(page.content()):
        raise RuntimeError(
            "GSCCCA session expired after search — re-run get_gsccca_cookie.py"
        )

    # Grantor/grantee data is loaded via AJAX when records are expanded.
    # "Expand All Details" triggers a postback that populates the lvExpandedGrantor/Grantee spans.
    expand_btn = page.query_selector("#BodyContent_lvDashboard_btnExpandAllDetails")
    if expand_btn:
        expand_btn.click()
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeout:
            page.wait_for_load_state("domcontentloaded", timeout=5000)

    html = page.content()
    url  = page.url

    records, total = _parse_dashboard_html(html, county, instrument_name, url)

    if total == 0:
        logger.debug("No results: %s / %s %s–%s", county, instrument_name, date_from, date_to)
        return []

    total_pages = _parse_total_pages(html)
    logger.info(
        "Found %d records (%d page%s): %s / %s",
        total, total_pages, "s" if total_pages > 1 else "",
        county, instrument_name,
    )

    for page_num in range(2, total_pages + 1):
        time.sleep(random.uniform(1, 2))
        next_btn = page.query_selector("#BodyContent_lvDashboard_btnDashboardNextPageTop")
        if not next_btn:
            logger.warning(
                "Next Page link missing at page %d for %s / %s",
                page_num - 1, county, instrument_name,
            )
            break
        next_btn.click()
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeout:
            page.wait_for_load_state("domcontentloaded", timeout=10000)

        expand_btn = page.query_selector("#BodyContent_lvDashboard_btnExpandAllDetails")
        if expand_btn:
            expand_btn.click()
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except PlaywrightTimeout:
                page.wait_for_load_state("domcontentloaded", timeout=5000)

        page_records, _ = _parse_dashboard_html(page.content(), county, instrument_name, page.url)
        records.extend(page_records)

    return records


# ─── Orchestration ────────────────────────────────────────────────────────────

def run_scrape(
    counties_spec: str = "ALL",
    days_back: int = 3,
    tier: int | str = "both",
    cookie_source: str | None = None,
) -> list[dict]:
    """
    Full scrape run. Returns list of scored lead dicts sorted by lead_score desc.
    """
    if days_back > MAX_DATE_RANGE_DAYS:
        logger.warning(
            "days_back=%d exceeds GSCCCA max of %d — capping",
            days_back, MAX_DATE_RANGE_DAYS,
        )
        days_back = MAX_DATE_RANGE_DAYS

    cookies    = load_cookies(cookie_source)
    pw_cookies = _to_playwright_cookies(cookies)
    counties   = resolve_counties(counties_spec)
    date_to    = datetime.now().strftime("%m/%d/%Y")
    date_from  = (datetime.now() - timedelta(days=days_back)).strftime("%m/%d/%Y")

    if tier == 1 or tier == "1":
        instruments = TIER_1
    elif tier == 2 or tier == "2":
        instruments = TIER_2
    else:
        instruments = ALL_INSTRUMENTS

    leads: list[dict] = []
    total_combos = len(counties) * len(instruments)
    done = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        context.add_cookies(pw_cookies)
        scrape_page = context.new_page()

        try:
            for county in counties:
                for instrument_name, instrument_id in instruments.items():
                    done += 1
                    logger.info(
                        "[%d/%d] %s — %s", done, total_combos, county, instrument_name
                    )
                    try:
                        records = search_instrument(
                            scrape_page,
                            county, instrument_name, instrument_id,
                            date_from, date_to,
                        )
                        for lead in records:
                            lead["lead_score"] = score_lead(lead)
                            lead["notes"] = (
                                f"{lead['instrument_type']} | Score: {lead['lead_score']} | "
                                f"Filed: {lead['file_date']} | {lead['county']} County"
                            )
                            leads.append(lead)
                    except RuntimeError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Error scraping %s / %s: %s", county, instrument_name, exc
                        )

                    time.sleep(random.uniform(2, 4))
        finally:
            browser.close()

    leads.sort(key=lambda r: r.get("lead_score", 0), reverse=True)
    return leads


# ─── CSV export ───────────────────────────────────────────────────────────────

FIELDNAMES = [
    "grantor_name", "grantee_name", "instrument_type", "book_page",
    "file_date", "county", "parcel_id", "consideration_amount",
    "tier", "lead_score", "scraped_at", "source_url", "notes",
]


def write_csv(leads: list[dict], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(leads)
    logger.info("Wrote %d leads to %s", len(leads), output_path)
    return output_path


# ─── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    import argparse

    parser = argparse.ArgumentParser(description="GSCCCA lead scraper")
    parser.add_argument("--counties",  default="ALL")
    parser.add_argument("--days-back", type=int, default=3)
    parser.add_argument("--tier",      default="both", choices=["1", "2", "both"])
    parser.add_argument("--output",    default=None)
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    out   = Path(args.output) if args.output else Path(f"leads_{today}.csv")

    leads = run_scrape(
        counties_spec=args.counties,
        days_back=args.days_back,
        tier=args.tier,
    )
    write_csv(leads, out)
    print(f"\nDone. {len(leads)} leads → {out}")
