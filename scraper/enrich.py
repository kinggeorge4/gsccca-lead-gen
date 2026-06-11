"""
enrich.py — Property address lookup for GSCCCA lead records.

Queries county GIS REST APIs using deed book/page or legal description.
Only covers counties with confirmed public ArcGIS endpoints.
Fails silently — records are always returned, just without address if lookup fails.
"""

from __future__ import annotations

import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import json

logger = logging.getLogger(__name__)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

_ARCGIS_TIMEOUT = 8  # seconds per request


# ─── County GIS configuration ─────────────────────────────────────────────────

# Each entry: county name → (query_func_name, endpoint_url, ssl_bypass)
# query_func: "book_page" or "legal_desc"
_COUNTY_GIS: dict[str, dict] = {
    "GWINNETT": {
        "url": (
            "https://services3.arcgis.com/RfpmnkSAQleRbndX/arcgis/rest/services"
            "/Property_and_Tax/FeatureServer/3/query"
        ),
        "method": "doc_ref",
        "ssl_bypass": False,
        "fields": {"address": "LOCADDR", "city": "LOCCITY", "zip": "LOCZIP"},
        "doc_ref_field": "DOC1REF",
    },
    "CHEROKEE": {
        "url": (
            "https://gis.cherokeega.com/arcgis/rest/services"
            "/MainLayers/MapServer/1/query"
        ),
        "method": "book_page",
        "ssl_bypass": True,
        "fields": {
            "address": "Property_Address",
            "city": "Property_City",
            "zip": "Property_Zip",
        },
        "book_field": "DEEDBOOK",
        "page_field": "DEEDPAGE",
    },
}


# ─── Query helpers ─────────────────────────────────────────────────────────────

def _get_json(url: str, ssl_bypass: bool = False) -> dict:
    ctx = _SSL_CTX if ssl_bypass else None
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_ARCGIS_TIMEOUT, context=ctx) as r:
        return json.loads(r.read())


def _parse_book_page(book_page: str) -> tuple[str, str]:
    """Extract book and page from 'Book NNNNN Page MMM' string."""
    m = re.search(r"Book\s+(\d+)\s+Page\s+(\d+)", book_page, re.I)
    if m:
        return m.group(1), m.group(2)
    parts = book_page.split()
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return book_page, ""


def _query_gwinnett(county_cfg: dict, book: str, page: str, **kwargs) -> dict | None:
    """
    Query Gwinnett Property and Tax table.

    Strategy (in order):
    1. Subdivision + Lot  → most reliable, works even before GIS is updated
    2. Grantor last name + Subdivision  → fallback for ambiguous lots
    3. Book/Page via DOC1REF  → only works after GIS update cycle (~3-6 months lag)
    """
    subdivision = kwargs.get("subdivision", "").strip()
    lot         = kwargs.get("lot", "").strip()
    grantor     = kwargs.get("grantor_name", "").strip()
    district    = kwargs.get("district", "").strip()

    base_url = county_cfg["url"]
    out_fields = "LOCADDR,LOCCITY,LOCZIP,OWNER1,LEGAL1,DISTNUM"

    def _extract_addr(features: list) -> dict | None:
        if not features:
            return None
        attr = features[0].get("attributes", {})
        raw_addr = (attr.get("LOCADDR") or "").strip()
        city     = (attr.get("LOCCITY") or "").strip()
        zipcode  = str(attr.get("LOCZIP") or "").strip()
        if raw_addr and "," in raw_addr and not city:
            parts = raw_addr.rsplit(",", 1)
            raw_addr = parts[0].strip()
            rest = parts[1].strip()
            zip_m = re.search(r"\d{5}", rest)
            if zip_m:
                zipcode = zip_m.group(0)
                city = rest[: zip_m.start()].strip()
        if not raw_addr:
            return None
        return {
            "street_address": raw_addr.title(),
            "city": city.title() if city else "",
            "state": "GA",
            "zip_code": zipcode,
        }

    # ── Strategy 1: Subdivision + Lot ──────────────────────────────────────────
    if subdivision and lot:
        subdiv_short = " ".join(subdivision.split()[:3])
        where = (
            f"UPPER(LEGAL1) LIKE UPPER('%{subdiv_short}%') AND "
            f"(UPPER(LEGAL1) LIKE UPPER('L{lot} %') OR "
            f" UPPER(LEGAL1) LIKE UPPER('LOT {lot} %') OR "
            f" UPPER(LEGAL1) LIKE UPPER('% L{lot} %'))"
        )
        params = urllib.parse.urlencode({
            "where": where, "outFields": out_fields,
            "f": "json", "resultRecordCount": "5",
        })
        try:
            data = _get_json(f"{base_url}?{params}")
            features = data.get("features", [])
            # If single result, use it
            if len(features) == 1:
                return _extract_addr(features)
            # Multiple results: try to match by district number
            if len(features) > 1 and district:
                dist_matches = [
                    f for f in features
                    if str(f.get("attributes", {}).get("DISTNUM", "")).strip().lstrip("0")
                    == district.lstrip("0")
                ]
                if len(dist_matches) == 1:
                    return _extract_addr(dist_matches)
                features = dist_matches or features
            # Fall through to next strategy if still ambiguous
        except Exception:
            pass

    # ── Strategy 2: Grantor last name + Subdivision ─────────────────────────────
    if grantor and subdivision:
        last_name = grantor.split(",")[0].strip().split(";")[0].strip()
        if last_name and len(last_name) >= 4:
            subdiv_short = " ".join(subdivision.split()[:3])
            where = (
                f"UPPER(OWNER1) LIKE UPPER('%{last_name}%') AND "
                f"UPPER(LEGAL1) LIKE UPPER('%{subdiv_short}%')"
            )
            params = urllib.parse.urlencode({
                "where": where, "outFields": out_fields,
                "f": "json", "resultRecordCount": "3",
            })
            try:
                data = _get_json(f"{base_url}?{params}")
                features = data.get("features", [])
                if len(features) == 1:
                    return _extract_addr(features)
            except Exception:
                pass

    # ── Strategy 3: Book/Page via DOC1REF ──────────────────────────────────────
    if book and page:
        book_int = book.lstrip("0") or "0"
        page_int = page.lstrip("0") or "0"
        for where in [
            f"DOC1REF LIKE '{book_int} {page_int}%'",
            f"DOC1REF LIKE '{book_int}%{page_int}%'",
        ]:
            params = urllib.parse.urlencode({
                "where": where, "outFields": out_fields,
                "f": "json", "resultRecordCount": "3",
            })
            try:
                data = _get_json(f"{base_url}?{params}")
                features = data.get("features", [])
                if features:
                    return _extract_addr(features)
            except Exception:
                continue

    return None


def _query_cherokee(county_cfg: dict, book: str, page: str, **kwargs) -> dict | None:
    """Query Cherokee County parcel layer by deed book/page."""
    book_padded = book.zfill(5)
    page_padded = page.zfill(5)

    for where in [
        f"DEEDBOOK='{book_padded}' AND DEEDPAGE='{page_padded}'",
        f"DEEDBOOK='{book}' AND DEEDPAGE='{page}'",
        f"DEEDBOOK LIKE '%{book}%' AND DEEDPAGE LIKE '%{page}%'",
    ]:
        params = urllib.parse.urlencode({
            "where": where,
            "outFields": "Property_Address,Property_City,Property_Zip",
            "f": "json",
            "resultRecordCount": "3",
        })
        url = f"{county_cfg['url']}?{params}"
        try:
            data = _get_json(url, ssl_bypass=county_cfg["ssl_bypass"])
            features = data.get("features", [])
            if features:
                attr = features[0].get("attributes", {})
                addr = attr.get("Property_Address", "") or ""
                city = attr.get("Property_City", "") or ""
                zipcode = attr.get("Property_Zip", "") or ""
                if addr:
                    return {
                        "street_address": addr.title(),
                        "city": city.title() if city else "",
                        "state": "GA",
                        "zip_code": str(zipcode) if zipcode else "",
                    }
        except Exception:
            continue
    return None


# ─── Public API ───────────────────────────────────────────────────────────────

_COUNTY_QUERY_FN = {
    "GWINNETT": _query_gwinnett,
    "CHEROKEE": _query_cherokee,
}


def lookup_address(
    county: str,
    book_page: str,
    subdivision: str = "",
    lot: str = "",
    district: str = "",
    grantor_name: str = "",
) -> dict:
    """
    Attempt to find the property street address for a GSCCCA record.

    Returns a dict with keys: street_address, city, state, zip_code.
    All values are empty strings if the lookup fails or the county is
    not yet supported.
    """
    blank = {"street_address": "", "city": "", "state": "GA", "zip_code": ""}

    county_upper = county.upper().strip()
    cfg = _COUNTY_GIS.get(county_upper)
    if not cfg:
        return blank

    book, page = _parse_book_page(book_page)

    fn = _COUNTY_QUERY_FN.get(county_upper)
    if not fn:
        return blank

    try:
        result = fn(
            cfg, book, page,
            subdivision=subdivision,
            lot=lot,
            district=district,
            grantor_name=grantor_name,
        )
        if result:
            logger.debug("Address found for %s %s: %s", county, book_page, result)
            return result
    except Exception as exc:
        logger.debug("Address lookup failed for %s %s: %s", county, book_page, exc)

    return blank
