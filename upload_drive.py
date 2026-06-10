"""
upload_drive.py — Upload daily leads CSV to Google Drive.

Folder structure created automatically:
    Propstor Leads / GSCCCA / YYYY-MM / leads_YYYY-MM-DD.csv

Also appends to all_leads_master.csv in the GSCCCA root folder.

Usage:
    python upload_drive.py leads_2026-06-10.csv

Auth:
    Set GDRIVE_CREDS_JSON env var to service account JSON string,
    or place creds.json in repo root (gitignored).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
except ImportError:
    logger.error("google-api-python-client not installed. Run: pip install google-api-python-client google-auth")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/drive"]
ROOT_FOLDER_NAME = "Propstor Leads"
GSCCCA_FOLDER_NAME = "GSCCCA"
MASTER_FILENAME = "all_leads_master.csv"

FIELDNAMES = [
    "grantor_name", "grantee_name", "instrument_type", "book_page",
    "file_date", "county", "parcel_id", "consideration_amount",
    "tier", "lead_score", "scraped_at", "source_url", "notes",
]


def _load_creds() -> Credentials:
    raw = os.environ.get("GDRIVE_CREDS_JSON", "")
    if not raw:
        creds_file = Path(__file__).parent / "creds.json"
        if creds_file.exists():
            raw = creds_file.read_text()
        else:
            raise FileNotFoundError(
                "No Google Drive credentials found. "
                "Set GDRIVE_CREDS_JSON env var or place creds.json in repo root."
            )
    info = json.loads(raw)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _get_or_create_folder(service, name: str, parent_id: str | None = None) -> str:
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]
    folder = service.files().create(body=meta, fields="id").execute()
    logger.info("Created Drive folder: %s", name)
    return folder["id"]


def _find_file(service, name: str, parent_id: str) -> str | None:
    query = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def upload_leads(csv_path: Path) -> None:
    creds = _load_creds()
    service = build("drive", "v3", credentials=creds)

    # Build/resolve folder path: Propstor Leads / GSCCCA / YYYY-MM
    month_tag = datetime.now().strftime("%Y-%m")
    root_id = _get_or_create_folder(service, ROOT_FOLDER_NAME)
    gsccca_id = _get_or_create_folder(service, GSCCCA_FOLDER_NAME, root_id)
    month_id = _get_or_create_folder(service, month_tag, gsccca_id)

    # Upload the daily CSV (replace if exists)
    filename = csv_path.name
    existing_id = _find_file(service, filename, month_id)
    media = MediaIoBaseUpload(
        io.BytesIO(csv_path.read_bytes()),
        mimetype="text/csv",
        resumable=True,
    )
    if existing_id:
        service.files().update(fileId=existing_id, media_body=media).execute()
        logger.info("Updated %s in Drive (id=%s)", filename, existing_id)
    else:
        meta = {"name": filename, "parents": [month_id]}
        service.files().create(body=meta, media_body=media, fields="id").execute()
        logger.info("Uploaded %s to Drive", filename)

    # Append to master CSV
    _append_to_master(service, gsccca_id, csv_path)


def _append_to_master(service, gsccca_folder_id: str, new_csv: Path) -> None:
    master_id = _find_file(service, MASTER_FILENAME, gsccca_folder_id)

    new_rows = list(csv.DictReader(new_csv.open(encoding="utf-8")))

    if master_id:
        # Download existing master
        request = service.files().get_media(fileId=master_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        existing_rows = list(csv.DictReader(io.TextIOWrapper(buf, encoding="utf-8")))
    else:
        existing_rows = []

    all_rows = existing_rows + new_rows

    out_buf = io.StringIO()
    writer = csv.DictWriter(out_buf, fieldnames=FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(all_rows)
    encoded = out_buf.getvalue().encode("utf-8")

    media = MediaIoBaseUpload(io.BytesIO(encoded), mimetype="text/csv", resumable=True)
    if master_id:
        service.files().update(fileId=master_id, media_body=media).execute()
    else:
        meta = {"name": MASTER_FILENAME, "parents": [gsccca_folder_id]}
        service.files().create(body=meta, media_body=media, fields="id").execute()

    logger.info("Master CSV updated: %d total records", len(all_rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("Usage: python upload_drive.py <leads_YYYY-MM-DD.csv>")
        sys.exit(1)
    upload_leads(Path(sys.argv[1]))
