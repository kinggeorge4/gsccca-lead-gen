"""
upload_sheets.py — Append daily GSCCCA leads to a Google Sheet.

Tab layout:
  "All Leads"   — running master, deduplicated by (county + book_page)
  "Latest Run"  — overwritten each run with today's results only

Auth:
    Set GDRIVE_CREDS_JSON env var to service account JSON string,
    or place creds.json in repo root (gitignored).

Usage:
    python upload_sheets.py leads_2026-06-11.csv
    GSHEET_ID=1abc...xyz python upload_sheets.py leads_2026-06-11.csv
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Columns written to the sheet (in order)
SHEET_COLUMNS = [
    "file_date", "county", "instrument_type", "tier", "lead_score",
    "grantor_first_name", "grantor_last_name", "grantor_name",
    "street_address", "city", "state", "zip_code",
    "grantee_name", "consideration_amount",
    "subdivision", "district", "land_lot", "lot",
    "book_page", "parcel_id", "scraped_at", "notes", "source_url",
]

ALL_LEADS_TAB  = "All Leads"
LATEST_RUN_TAB = "Latest Run"


# ─── Auth ─────────────────────────────────────────────────────────────────────

def _load_creds():
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GDRIVE_CREDS_JSON", "")
    if not raw:
        creds_file = Path(__file__).parent / "creds.json"
        if creds_file.exists():
            raw = creds_file.read_text()
        else:
            raise FileNotFoundError(
                "No credentials found. Set GDRIVE_CREDS_JSON env var "
                "or place creds.json in repo root."
            )
    return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)


def _get_client():
    import gspread
    return gspread.authorize(_load_creds())


# ─── Sheet helpers ─────────────────────────────────────────────────────────────

def _get_or_create_tab(sheet, title: str, headers: list[str]):
    """Return worksheet by title, creating it with a header row if needed."""
    try:
        ws = sheet.worksheet(title)
    except Exception:
        ws = sheet.add_worksheet(title=title, rows=1, cols=len(headers))
        ws.append_row(headers, value_input_option="RAW")
    return ws


def _existing_keys(ws) -> set[str]:
    """Return set of 'county|book_page' strings already in the sheet."""
    rows = ws.get_all_values()
    if len(rows) < 2:
        return set()
    try:
        hdr = rows[0]
        ci = hdr.index("county")
        bi = hdr.index("book_page")
    except ValueError:
        return set()
    return {f"{r[ci]}|{r[bi]}" for r in rows[1:] if len(r) > max(ci, bi)}


def _rows_to_append(leads: list[dict], existing_keys: set[str]) -> list[list]:
    """Filter out duplicates and convert to row lists."""
    out = []
    for r in leads:
        key = f"{r.get('county','')}|{r.get('book_page','')}"
        if key in existing_keys:
            continue
        out.append([str(r.get(col, "") or "") for col in SHEET_COLUMNS])
    return out


# ─── Main upload ──────────────────────────────────────────────────────────────

def upload_to_sheet(csv_path: Path, sheet_id: str) -> None:
    gc = _get_client()
    sheet = gc.open_by_key(sheet_id)

    leads = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    if not leads:
        logger.info("No leads in %s — nothing to upload", csv_path.name)
        return

    # ── All Leads tab (append, deduplicated) ──────────────────────────────────
    all_ws = _get_or_create_tab(sheet, ALL_LEADS_TAB, SHEET_COLUMNS)
    existing = _existing_keys(all_ws)
    new_rows = _rows_to_append(leads, existing)

    if new_rows:
        all_ws.append_rows(new_rows, value_input_option="USER_ENTERED")
        logger.info("Appended %d new rows to '%s'", len(new_rows), ALL_LEADS_TAB)
    else:
        logger.info("All %d leads already in '%s' — no new rows", len(leads), ALL_LEADS_TAB)

    # ── Latest Run tab (overwrite) ────────────────────────────────────────────
    try:
        latest_ws = sheet.worksheet(LATEST_RUN_TAB)
        latest_ws.clear()
    except Exception:
        latest_ws = sheet.add_worksheet(title=LATEST_RUN_TAB, rows=1, cols=len(SHEET_COLUMNS))

    all_data = [SHEET_COLUMNS] + [[str(r.get(col, "") or "") for col in SHEET_COLUMNS] for r in leads]
    latest_ws.update(all_data, value_input_option="USER_ENTERED")
    logger.info("Wrote %d rows to '%s'", len(leads), LATEST_RUN_TAB)

    logger.info("Sheet updated: %d total new leads", len(new_rows))


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python upload_sheets.py <leads_YYYY-MM-DD.csv>")
        sys.exit(1)

    sheet_id = os.environ.get("GSHEET_ID", "")
    if not sheet_id:
        print("Error: set GSHEET_ID env var to your Google Sheet ID")
        sys.exit(1)

    upload_to_sheet(Path(sys.argv[1]), sheet_id)
