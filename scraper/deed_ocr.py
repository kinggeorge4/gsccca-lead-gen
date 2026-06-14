"""
deed_ocr.py — Address extraction from GSCCCA PT-61 deed forms via OCR.

Flow:
  1. Navigate to GSCCCA book/page search → follow redirect chain to final.asp
  2. On final.asp, extract the PT-61 EFLNO from the showPT61() JS call
  3. Load the PT-61 viewer (ImageMain.aspx?key1=PT61EFLNO&countyname=COUNTY)
  4. Pull the rendered deed canvas as a high-res PIL Image (1700×2280+)
  5. Run pytesseract; parse Section A mailing address + Section D property info
  6. Return street_address / city / state / zip_code dict

PT-61 (Georgia Real Estate Transfer Tax Return) is filed with every deed
transfer. Section A holds the SELLER mailing address (= property address for
individuals), Section D holds the property location (often blank by filers).

Rate-limit note: The GSCCCA imaging system throttles GetImage.aspx requests.
The PT-61 path (appid=35, key1=EFLNO) has been observed to avoid the rate limit
that affects the main deed viewer (appid=4, id=Key).

Fails silently — returns None if no PT-61 found or session is expired.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_RATE_LIMIT_PHRASE = "rate at which you are requesting images has exceeded"

# Street types used in the generic street-number pattern
_STREET_TYPES = (
    "ST", "AVE", "RD", "DR", "LN", "CT", "WAY", "BLVD", "PL", "TRL",
    "CIR", "PKWY", "HWY", "PATH", "WALK", "XING", "RUN", "RIDGE", "PT",
    "STREET", "AVENUE", "ROAD", "DRIVE", "LANE", "COURT", "BOULEVARD",
    "PLACE", "TRAIL", "CIRCLE", "PARKWAY", "HIGHWAY", "CROSSING",
)

# Ordered list of address-trigger patterns (tries each in sequence)
_ADDR_TRIGGERS = [
    r"known\s+as\s+([^\n,]{5,80})",
    r"street\s+address\s+(?:of\s+|is\s+)?([0-9][^\n,]{4,80})",
    r"property\s+address[:\s]+([0-9][^\n,]{4,80})",
    r"commonly\s+known\s+as\s+([^\n,]{5,80})",
    r"located\s+at\s+([0-9][^\n,]{4,80})",
    r"situate[d]?\s+at\s+([0-9][^\n,]{4,80})",
    # Generic: street-number followed by street name ending with a known type.
    # [a-zA-Z]* (not +) allows single-letter directional prefixes (N, S, E, W).
    r"(\d{1,5}\s+[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*){0,3}\s+(?:"
    + "|".join(_STREET_TYPES)
    + r")[\s.,]{1,3})",
]

# PT-61 Section D / "Location of Property" — marks the property address block.
# These labels appear AFTER Section A (seller mailing address) in form order,
# so matching here avoids returning a bank's out-of-state mailing address.
_SECTION_D_MARKERS = (
    r"location\s+of\s+property",
    r"street\s+address\s+of\s+(?:the\s+)?property",
    r"section\s+d\b",
    r"property\s+address\s*:",
)

# After a Section D marker, stop reading at these (indicates next form section)
_SECTION_D_END_MARKERS = (
    r"section\s+[e-z]\b",
    r"calculation\s+of",
    r"date\s+of\s+(?:deed|sale|transfer)",
    r"transfer\s+tax",
    r"state\s+tax\s+due",
)

# Matches a two-letter state abbreviation (not GA) followed by a zip code.
# Used to detect out-of-state addresses (bank/lender Section A entries).
_NON_GA_STATE_RE = re.compile(r"\b(?!GA\b)[A-Z]{2}\s+\d{5}\b")


# ─── Low-level helpers ────────────────────────────────────────────────────────

def _get_canvas_image(page) -> "Image.Image | None":
    """
    Extract the HTML5 viewer canvas as a PIL Image.

    The GSCCCA viewer renders encrypted TIFFs into a <canvas> element.
    Pulling canvas.toDataURL() gives a lossless PNG of the decoded image.
    """
    from PIL import Image

    try:
        result = page.evaluate("""() => {
            const canvas = document.querySelector('canvas');
            if (!canvas) return {found: false};
            return {found: true, data: canvas.toDataURL('image/png'),
                    w: canvas.width, h: canvas.height};
        }""")
    except Exception:
        return None

    if not result.get("found") or not result.get("data"):
        return None

    data_url = result["data"]
    if "," not in data_url:
        return None

    img_bytes = base64.b64decode(data_url.split(",")[1])
    try:
        return Image.open(io.BytesIO(img_bytes))
    except Exception:
        return None


def _take_screenshot_image(page) -> "Image.Image":
    """Fallback: viewport screenshot when canvas extraction fails."""
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "deed_shot.png"
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.screenshot(path=str(path))
        return Image.open(path).copy()


def _wait_for_viewer(page, timeout_ms: int = 12000) -> None:
    """Wait for the HTML5 viewer to finish rendering the deed image."""
    try:
        # vtu.js signals readiness by populating the canvas
        page.wait_for_function(
            "() => { const c = document.querySelector('canvas'); return c && c.width > 200; }",
            timeout=timeout_ms,
        )
    except Exception:
        # Fall back to networkidle if canvas check times out
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass


def _is_rate_limited_text(text: str) -> bool:
    return _RATE_LIMIT_PHRASE in text.lower()


# ─── Address parsing ──────────────────────────────────────────────────────────

def _find_section_d(clean: str) -> str | None:
    """
    Return the substring covering Section D / 'Location of Property' block.

    Clips at the next section boundary or 500 chars, whichever comes first.
    Section D appears after Section A (seller mailing address) in PT-61 forms,
    so searching here avoids matching the bank/lender address for foreclosures.
    """
    for marker in _SECTION_D_MARKERS:
        m = re.search(marker, clean, re.I)
        if not m:
            continue
        start = m.end()
        end = min(start + 500, len(clean))
        for end_pat in _SECTION_D_END_MARKERS:
            em = re.search(end_pat, clean[start:], re.I)
            if em:
                end = min(end, start + em.start())
        snippet = clean[start:end].strip()
        if snippet:
            return snippet
    return None


def _extract_street_address(clean: str) -> str | None:
    """
    Return the property street address from PT-61 OCR text.

    Priority order:
    1. Section D / "Location of Property" block — avoids the seller's mailing
       address (Section A) which is a bank address for institutional grantors.
    2. Labeled contextual patterns on full text, skipped when a non-GA state
       code appears in the immediate vicinity (bank address signal).
    3. Generic street-number pattern — accepted only when a GA zip is nearby.
    """
    # ── 1. Section D block (property address, comes after bank address) ─────
    section_d = _find_section_d(clean)
    if section_d:
        for pat in _ADDR_TRIGGERS:
            m = re.search(pat, section_d, re.I)
            if m:
                candidate = m.group(1).strip().rstrip(".,;")
                if re.search(r"\d", candidate) and 8 <= len(candidate) <= 120:
                    return candidate

    # ── 2. Labeled patterns on full text — reject if non-GA state nearby ───
    for pat in _ADDR_TRIGGERS[:-1]:  # exclude generic fallback
        m = re.search(pat, clean, re.I)
        if m:
            candidate = m.group(1).strip().rstrip(".,;")
            if re.search(r"\d", candidate) and 8 <= len(candidate) <= 120:
                vicinity = clean[m.start() : m.end() + 120]
                if not _NON_GA_STATE_RE.search(vicinity):
                    return candidate

    # ── 3. Generic fallback — accept only when GA zip is nearby ─────────────
    for m in re.finditer(_ADDR_TRIGGERS[-1], clean, re.I):
        candidate = m.group(1).strip().rstrip(".,;")
        if re.search(r"\d", candidate) and 8 <= len(candidate) <= 120:
            vicinity = clean[max(0, m.start() - 30) : m.end() + 150]
            if re.search(r"\bGA\s+\d{5}\b", vicinity, re.I):
                return candidate

    return None


def _extract_city_zip(clean: str) -> tuple[str, str]:
    """
    Return (city, zip_code) from cleaned OCR text.

    Prefers title-case city names (individual sellers) over ALL-CAPS (corporate).
    Skips matches where the city token contains "County" — those are OCR
    artifacts from the county header printed on deed forms, not real city names.
    """
    def _ok_city(name: str) -> bool:
        return "county" not in name.lower()

    # Title-case first (e.g. "Acworth, GA 30101")
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s*,\s*GA\s+(\d{5})", clean):
        city = m.group(1).strip()
        if _ok_city(city):
            return city, m.group(2)
    # All-caps fallback (e.g. "MARIETTA, GA 30066" from corporate buyer section)
    for m in re.finditer(r"\b([A-Z]{2,}(?:\s+[A-Z]+){0,2})\s*,\s*GA\s+(\d{5})", clean):
        city = m.group(1).strip().title()
        if _ok_city(city):
            return city, m.group(2)
    return "", ""


def _extract_parcel_id(clean: str, county_name: str = "") -> str:
    """Return a Cobb-style parcel ID if present (bonus field)."""
    name = (county_name or "COBB").upper()
    m = re.search(rf"\b{re.escape(name)}\s+(\d{{8,15}})", clean, re.I)
    return m.group(1) if m else ""


def _parse_pt61_address(text: str, county_name: str = "") -> dict | None:
    """
    Parse a PT-61 OCR dump into an address dict.

    Returns {"street_address", "city", "state", "zip_code"} or None.
    """
    clean = re.sub(r"\s+", " ", text).strip()

    street = _extract_street_address(clean)
    if not street:
        return None

    city, zipcode = _extract_city_zip(clean)
    return {
        "street_address": street,
        "city": city,
        "state": "GA",
        "zip_code": zipcode,
    }


# ─── PT-61 lookup ─────────────────────────────────────────────────────────────

def _ocr_from_viewer(page, url: str) -> str | None:
    """
    Load url in page, extract canvas, run OCR. Returns raw text or None.
    Uses networkidle to ensure the encrypted TIFF is fully decoded and painted
    before reading the canvas.
    """
    import pytesseract

    # networkidle waits for GetImage.aspx to complete before we read the canvas
    page.goto(url, wait_until="networkidle", timeout=30000)

    if "login" in page.url.lower():
        logger.debug("Session expired loading viewer: %s", url)
        return None

    img = _get_canvas_image(page)
    if img is None:
        img = _take_screenshot_image(page)

    raw_text = pytesseract.image_to_string(img, config="--psm 6 --oem 3")
    logger.debug("OCR text (%d chars): %s …", len(raw_text), raw_text[:300])
    return raw_text


def _get_pt61_key(page_html: str) -> tuple[str, str] | None:
    """
    Extract the PT-61 EFLNO and county name from final.asp page HTML.

    Looks for: showPT61('0332026010102', 'COBB')
    Returns (eflno, county_name) or None if not found.
    """
    m = re.search(
        r"showPT61\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
        page_html,
    )
    return (m.group(1), m.group(2)) if m else None


# ─── Public API ───────────────────────────────────────────────────────────────

def lookup_address_via_ocr(
    page,
    county_id: int,
    book: str,
    page_num: str,
) -> dict | None:
    """
    Navigate the GSCCCA book/page search and OCR the PT-61 deed form.

    Args:
        page:       Playwright page with authenticated session (both domains)
        county_id:  GSCCCA integer county ID (same as Premium search dropdown)
        book:       Deed book number string
        page_num:   Deed page number string

    Returns:
        {"street_address", "city", "state", "zip_code"} or None.
    """
    try:
        return _do_ocr_lookup(page, county_id, book, page_num)
    except Exception as exc:
        logger.debug(
            "OCR lookup failed for county_id=%s book=%s page=%s: %s",
            county_id, book, page_num, exc,
        )
        return None


def _do_ocr_lookup(page, county_id: int, book: str, page_num: str) -> dict | None:
    import time as _time

    # ── Step 1: Navigate to book/page search form ──────────────────────────
    page.goto(
        "https://search.gsccca.org/RealEstate/bookpagesearch.asp",
        wait_until="domcontentloaded",
        timeout=15000,
    )
    if "login" in page.url.lower():
        logger.debug("Session expired at bookpagesearch.asp")
        return None

    # ── Step 2: Fill and submit SearchType form ────────────────────────────
    page.select_option("select[name='intCountyID']", value=str(county_id))
    page.fill("input[name='txtBook']", book.lstrip("0") or "0")
    page.fill("input[name='txtPage']", page_num.lstrip("0") or "0")

    # SearchType form → rebooks.asp → JS auto-submits frmLogin →
    # apps.gsccca.org/login.asp → apps.gsccca.org/realestate/rebooks.asp → final.asp
    with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
        page.evaluate("document.SearchType.submit()")

    try:
        page.wait_for_url(re.compile(r"final\.asp", re.I), timeout=20000)
    except Exception:
        url_now = page.url
        if "login" in url_now.lower():
            logger.debug("Redirected to login — session expired")
            return None
        logger.debug("Did not reach final.asp; current url=%s", url_now)

    if "login" in page.url.lower():
        return None

    # ── Step 3: Extract PT-61 EFLNO from final.asp ────────────────────────
    html = page.content()
    pt61_info = _get_pt61_key(html)

    if pt61_info:
        pt61_eflno, county_name = pt61_info
        logger.debug("Found PT-61 EFLNO=%s county=%s", pt61_eflno, county_name)

        # ── Step 4: Load PT-61 viewer and OCR ─────────────────────────────
        pt61_url = (
            f"https://search.gsccca.org/imaging/ImageMain.aspx"
            f"?key1={pt61_eflno}&countyname={county_name}&appid=35"
        )
        raw_text = _ocr_from_viewer(page, pt61_url)

        if raw_text and not _is_rate_limited_text(raw_text):
            result = _parse_pt61_address(raw_text, county_name)
            if result:
                logger.info(
                    "PT-61 OCR address for book=%s page=%s: %s",
                    book, page_num, result,
                )
                return result
            logger.debug("PT-61 OCR produced no address match")
        elif raw_text and _is_rate_limited_text(raw_text):
            logger.debug("PT-61 imaging rate-limited (key=%s)", pt61_eflno)

    else:
        logger.debug("No PT-61 found on final.asp for book=%s page=%s", book, page_num)

    # ── Step 5: Fallback — main deed viewer ───────────────────────────────
    # Extract document Key from final.asp URL or content
    final_url = page.url
    key_match = re.search(r"[?&][Kk]ey=(\d+)", final_url)
    if not key_match:
        key_match = re.search(r"[Kk]ey=(\d+)", html)
    if not key_match:
        logger.debug("No document Key found on final.asp")
        return None

    key = key_match.group(1)
    _RETRY_WAITS = (15, 40)

    for attempt, wait_secs in enumerate((*_RETRY_WAITS, None), start=1):
        deed_url = (
            f"https://search.gsccca.org/imaging/ImageMain.aspx"
            f"?id={key}&key1={book}&key2={page_num}&county={county_id}&appid=4"
        )
        raw_text = _ocr_from_viewer(page, deed_url)

        if raw_text is None:
            return None
        if not _is_rate_limited_text(raw_text):
            result = _parse_pt61_address(raw_text)
            if result:
                logger.info("Deed OCR address for key=%s: %s", key, result)
            return result

        if wait_secs is None:
            logger.debug("Deed imaging rate-limit persists after %d tries", attempt)
            return None

        logger.debug("Rate-limited on deed viewer (attempt %d) — waiting %ds", attempt, wait_secs)
        _time.sleep(wait_secs)

    return None


# ─── Batch second-pass enrichment ────────────────────────────────────────────

def enrich_missing_addresses(page, leads: list[dict], county_ids: dict[str, int]) -> None:
    """
    Second-pass OCR enrichment for leads that have no street_address.

    Skips counties already covered by GIS in enrich.py.
    Mutates leads in-place.

    Args:
        page:       Playwright page with active authenticated session
        leads:      Lead dicts — mutated in-place
        county_ids: County name → GSCCCA integer county ID
    """
    candidates = [
        r for r in leads
        if not r.get("street_address") and r.get("book_page")
    ]

    if not candidates:
        return

    logger.info("OCR second pass: %d records without address", len(candidates))

    # Track addresses seen across counties during this pass.
    # An address returned for 3+ different counties is a servicer/attorney
    # address printed on every deed — not the actual property address.
    _addr_county_seen: dict[str, set[str]] = {}
    _SERVICER_THRESHOLD = 3

    for lead in candidates:
        county = lead.get("county", "").upper()
        county_id = county_ids.get(county)
        if not county_id:
            logger.debug("No county ID for %s — skipping OCR", county)
            continue

        bp = lead.get("book_page", "")
        m = re.search(r"Book\s+(\d+)\s+Page\s+(\d+)", bp, re.I)
        if not m:
            continue
        book, page_num = m.group(1), m.group(2)

        addr = lookup_address_via_ocr(page, county_id, book, page_num)
        if not addr:
            continue

        street = addr.get("street_address", "")
        if street:
            key = street.lower().strip()
            _addr_county_seen.setdefault(key, set()).add(county)
            if len(_addr_county_seen[key]) >= _SERVICER_THRESHOLD:
                logger.debug(
                    "Skipping servicer/attorney address '%s' (seen in %d counties)",
                    street, len(_addr_county_seen[key]),
                )
                continue

        lead.update({k: v for k, v in addr.items() if v})
        logger.info("OCR enriched %s %s → %s", county, bp, street)


# ─── Standalone test ──────────────────────────────────────────────────────────

def test_ocr_single(
    county_id: int,
    book: str,
    page_num: str,
    cookies_path: str = "cookies.json",
) -> None:
    """Standalone test: OCR one deed and print result."""
    import json
    from playwright.sync_api import sync_playwright

    cookies = json.loads(Path(cookies_path).read_text())
    pw_cookies = [
        {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": "/"}
        for c in cookies
    ]

    logging.basicConfig(level=logging.DEBUG)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            # 2400px tall so the full PT-61 form renders in the canvas
            viewport={"width": 1280, "height": 2400},
        )
        ctx.add_cookies(pw_cookies)
        pg = ctx.new_page()

        result = lookup_address_via_ocr(pg, county_id, book, page_num)
        print(f"\nOCR result: {result!r}")
        browser.close()


if __name__ == "__main__":
    import sys

    # Usage: python -m scraper.deed_ocr <county_id> <book> <page>
    # Example (Cobb 16366/175): python -m scraper.deed_ocr 33 16366 175
    county_id = int(sys.argv[1]) if len(sys.argv) > 1 else 33
    book      = sys.argv[2] if len(sys.argv) > 2 else "16366"
    page_num  = sys.argv[3] if len(sys.argv) > 3 else "175"
    test_ocr_single(county_id, book, page_num)
