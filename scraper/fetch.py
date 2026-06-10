"""
fetch.py — Core GSCCCA scraper.

Injects session cookies captured by get_gsccca_cookie.py (never logs in),
then iterates instrument types × counties × date range, handling ASP.NET
ViewState and pagination automatically.
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
from typing import Generator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

try:
    import cloudscraper
    _make_session = cloudscraper.create_scraper
except ImportError:
    import requests
    _make_session = requests.Session

import requests as _requests  # always available for type hints

from .counties import GA_COUNTIES, resolve_counties
from .instruments import ALL_INSTRUMENTS, TIER_1, TIER_2, get_tier, normalize_instrument
from .score import score_lead

logger = logging.getLogger(__name__)

BASE_RE_URL = "https://search.gsccca.org/RealEstate/InstrumentTypeSearch.aspx"
BASE_LIEN_URL = "https://search.gsccca.org/Lien/namesearch.asp"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

COOKIE_MAX_AGE_HOURS = 12


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
                "No cookies found. Run get_gsccca_cookie.py first, or set GSCCCA_COOKIES env var."
            )

    # raw might be a file path string rather than JSON
    if raw.strip().startswith("[") or raw.strip().startswith("{"):
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


def _build_session(cookies: list[dict]):
    session = _make_session()
    session.headers.update({"User-Agent": USER_AGENT})
    for c in cookies:
        session.cookies.set(
            c["name"],
            c["value"],
            domain=c.get("domain", ".gsccca.org"),
        )
    return session


# ─── ASP.NET ViewState helpers ──────────────────────────────────────────────

def _parse_viewstate(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    fields = {}
    for field in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        tag = soup.find("input", {"name": field})
        if tag:
            fields[field] = tag.get("value", "")
    return fields


def _check_session_expired(html: str) -> bool:
    return "<title>" in html.lower() and "login" in html.lower()


# ─── Result parsing ─────────────────────────────────────────────────────────

_BOOK_PAGE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_AMOUNT_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?|\bno consideration\b|\blove and affection\b", re.I)


def _parse_results_page(html: str, county: str, instrument: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    records = []

    # Results are in a table with id containing "GridView" or class "results"
    table = soup.find("table", id=re.compile(r"Grid|grid|results", re.I))
    if not table:
        # Fallback: first table with more than 2 rows
        tables = soup.find_all("table")
        table = next((t for t in tables if len(t.find_all("tr")) > 2), None)

    if not table:
        return records

    rows = table.find_all("tr")[1:]  # skip header
    tier = get_tier(instrument)

    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 3:
            continue

        link_tag = row.find("a", href=True)
        record_url = urljoin(source_url, link_tag["href"]) if link_tag else source_url

        # Column positions vary slightly — parse by common patterns
        record = _extract_fields(cells, county, instrument, tier, record_url)
        if record:
            records.append(record)

    return records


def _extract_fields(
    cells: list[str],
    county: str,
    instrument: str,
    tier: int | None,
    source_url: str,
) -> dict | None:
    """
    Map table cells to lead fields. GSCCCA returns columns approximately as:
    [Book/Page, File Date, Grantor, Grantee, Instrument Type, Consideration, Parcel ID?]
    Exact order varies by search type; we parse by content heuristic.
    """
    # Need at least grantor + grantee
    if len(cells) < 3:
        return None

    # Attempt ordered mapping (most common GSCCCA layout)
    def _maybe(idx: int) -> str:
        return cells[idx].strip() if idx < len(cells) else ""

    book_page = ""
    file_date = ""
    grantor = ""
    grantee = ""
    parcel_id = ""
    consideration = ""

    for i, cell in enumerate(cells):
        if _BOOK_PAGE_RE.match(cell):
            book_page = cell
        elif re.match(r"\d{1,2}/\d{1,2}/\d{4}", cell):
            file_date = cell
        elif not grantor and len(cell) > 3 and i > 0:
            grantor = cell
        elif not grantee and len(cell) > 3 and i > 1 and cell != grantor:
            grantee = cell
        elif re.match(r"\d{2}-\d{3}-", cell) or re.match(r"[A-Z]\d{3}", cell):
            parcel_id = cell
        elif _AMOUNT_RE.search(cell):
            consideration = cell

    if not grantor and not grantee:
        return None

    return {
        "grantor_name": grantor,
        "grantee_name": grantee,
        "instrument_type": normalize_instrument(instrument),
        "book_page": book_page,
        "file_date": file_date,
        "county": county,
        "parcel_id": parcel_id,
        "consideration_amount": consideration,
        "tier": tier or 0,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
    }


def _parse_total_records(html: str) -> int:
    """Extract total record count from GSCCCA result header."""
    soup = BeautifulSoup(html, "lxml")
    # Look for "X records found" or "Showing 1-25 of X"
    for text in soup.stripped_strings:
        m = re.search(r"(\d+)\s+records?\s+found", text, re.I)
        if m:
            return int(m.group(1))
        m = re.search(r"of\s+(\d+)", text, re.I)
        if m:
            return int(m.group(1))
    return 0


# ─── Main search function ───────────────────────────────────────────────────

def search_instrument(
    session,
    county: str,
    instrument: str,
    date_from: str,
    date_to: str,
) -> Generator[dict, None, None]:
    """
    Yield all lead records for one county × instrument combination.
    Handles multi-page results via ASP.NET postback pagination.
    """
    # Step 1: GET the search form to capture ViewState tokens
    resp = session.get(BASE_RE_URL, timeout=30)
    resp.raise_for_status()

    if _check_session_expired(resp.text):
        raise RuntimeError("GSCCCA session expired — re-run get_gsccca_cookie.py")

    viewstate = _parse_viewstate(resp.text)

    if not viewstate.get("__VIEWSTATE"):
        logger.warning("No ViewState found for %s / %s — skipping", county, instrument)
        return

    # Step 2: POST the search
    form_data = {
        "__VIEWSTATE": viewstate.get("__VIEWSTATE", ""),
        "__VIEWSTATEGENERATOR": viewstate.get("__VIEWSTATEGENERATOR", ""),
        "__EVENTVALIDATION": viewstate.get("__EVENTVALIDATION", ""),
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "ctl00$ContentPlaceHolder1$ddlCounty": county,
        "ctl00$ContentPlaceHolder1$ddlInstrumentType": instrument,
        "ctl00$ContentPlaceHolder1$txtFromDate": date_from,
        "ctl00$ContentPlaceHolder1$txtToDate": date_to,
        "ctl00$ContentPlaceHolder1$btnSearch": "Search",
    }

    time.sleep(random.uniform(2, 4))
    resp = session.post(BASE_RE_URL, data=form_data, timeout=30)
    resp.raise_for_status()

    if _check_session_expired(resp.text):
        raise RuntimeError("GSCCCA session expired after POST — re-run get_gsccca_cookie.py")

    total = _parse_total_records(resp.text)
    if total == 0:
        # Distinguish no results from blocked
        if "login" in resp.text.lower():
            raise RuntimeError("Cookie expired — GSCCCA redirected to login")
        logger.debug("No results: %s / %s %s–%s", county, instrument, date_from, date_to)
        return

    logger.info("Found %d records: %s / %s", total, county, instrument)

    page_records = _parse_results_page(resp.text, county, instrument, BASE_RE_URL)
    yield from page_records

    # Paginate: 25 records per page
    pages = (total + 24) // 25
    for page_num in range(2, pages + 1):
        time.sleep(random.uniform(1, 2))
        vs = _parse_viewstate(resp.text)
        page_data = {
            "__VIEWSTATE": vs.get("__VIEWSTATE", ""),
            "__VIEWSTATEGENERATOR": vs.get("__VIEWSTATEGENERATOR", ""),
            "__EVENTVALIDATION": vs.get("__EVENTVALIDATION", ""),
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$GridView1",
            "__EVENTARGUMENT": f"Page${page_num}",
        }
        resp = session.post(BASE_RE_URL, data=page_data, timeout=30)
        resp.raise_for_status()
        page_records = _parse_results_page(resp.text, county, instrument, BASE_RE_URL)
        yield from page_records


# ─── Orchestration ───────────────────────────────────────────────────────────

def run_scrape(
    counties_spec: str = "ALL",
    days_back: int = 3,
    tier: int | str = "both",
    cookie_source: str | None = None,
) -> list[dict]:
    """
    Full scrape run. Returns list of scored lead dicts sorted by lead_score desc.

    Args:
        counties_spec: "ALL" or comma-separated county names
        days_back:     how many calendar days of filings to pull
        tier:          1, 2, or "both"
        cookie_source: JSON string, file path, or None (reads env/cookies.json)
    """
    cookies = load_cookies(cookie_source)
    session = _build_session(cookies)

    counties = resolve_counties(counties_spec)
    date_to = datetime.now().strftime("%m/%d/%Y")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%m/%d/%Y")

    if tier == 1 or tier == "1":
        instruments = TIER_1
    elif tier == 2 or tier == "2":
        instruments = TIER_2
    else:
        instruments = ALL_INSTRUMENTS

    leads: list[dict] = []
    total_combos = len(counties) * len(instruments)
    done = 0

    for county in counties:
        for instrument in instruments:
            done += 1
            logger.info("[%d/%d] %s — %s", done, total_combos, county, instrument)
            try:
                for lead in search_instrument(session, county, instrument, date_from, date_to):
                    lead["lead_score"] = score_lead(lead)
                    lead["notes"] = (
                        f"{lead['instrument_type']} | Score: {lead['lead_score']} | "
                        f"Filed: {lead['file_date']} | {lead['county']} County"
                    )
                    leads.append(lead)
            except RuntimeError as exc:
                logger.error("Session error: %s", exc)
                raise
            except Exception as exc:
                logger.warning("Error scraping %s / %s: %s", county, instrument, exc)

            time.sleep(random.uniform(2, 4))  # between county iterations

    leads.sort(key=lambda r: r.get("lead_score", 0), reverse=True)
    return leads


# ─── CSV export ──────────────────────────────────────────────────────────────

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


# ─── CLI entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    import argparse

    parser = argparse.ArgumentParser(description="GSCCCA lead scraper")
    parser.add_argument("--counties", default="ALL", help="ALL or comma-separated county names")
    parser.add_argument("--days-back", type=int, default=3)
    parser.add_argument("--tier", default="both", choices=["1", "2", "both"])
    parser.add_argument("--output", default=None, help="Output CSV path (default: leads_YYYY-MM-DD.csv)")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    out = Path(args.output) if args.output else Path(f"leads_{today}.csv")

    leads = run_scrape(
        counties_spec=args.counties,
        days_back=args.days_back,
        tier=args.tier,
    )

    write_csv(leads, out)
    print(f"\nDone. {len(leads)} leads → {out}")
