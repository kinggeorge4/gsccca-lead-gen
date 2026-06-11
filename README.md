# GSCCCA Lead Scraper — Propstor LLC

Automated daily scraper for distressed property leads from the Georgia Superior Court Clerks' Cooperative Authority ([search.gsccca.org](https://search.gsccca.org)).

---

## How It Works

1. **Cookie capture (local)** — `get_gsccca_cookie.py` opens a real Chromium window, you log in manually, and it saves your session cookie to `cookies.json`.
2. **Scraper (GitHub Actions)** — `scraper/fetch.py` injects those cookies into a headless Playwright browser, navigates the Premium Instrument Type Search, expands all result records, and exports a dated CSV. Raw HTTP POST is not used — the GSCCCA server performs browser-specific validation that requires a real browser session.
3. **Drive upload** — `upload_drive.py` pushes the CSV to `Propstor Leads / GSCCCA / YYYY-MM` in Google Drive and appends to `all_leads_master.csv`.
4. **Dashboard** — GitHub Pages renders `dashboard/index.html` with filterable, sortable leads from `leads.json`.

---

## Initial Setup

### 1. Install local dependencies

```bash
cd gsccca-lead-gen
pip install -r scraper/requirements.txt
pip install google-api-python-client
playwright install chromium
```

### 2. Capture your GSCCCA session cookie

```bash
python get_gsccca_cookie.py
```

- A Chromium window opens at `search.gsccca.org/RealEstatePremium/InstrumentTypeSearch.aspx` (auto-redirects to login)
- Log in with your GSCCCA Premium account credentials
- Press **ENTER** in the terminal once you see the Instrument Type Search page
- `cookies.json` is written to the repo root (gitignored)

### 3. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name | Value |
|---|---|
| `GSCCCA_COOKIES` | Paste the full contents of `cookies.json` |
| `GDRIVE_CREDS_JSON` | Paste your Google service account JSON |

### 4. Enable GitHub Pages

- Go to **Settings → Pages**
- Source: **Deploy from a branch**
- Branch: `gh-pages` / root

---

## Cookie Refresh SOP

GSCCCA sessions expire after approximately **12 hours**. Before each manual run or if the scraper returns zero results with "login" in the response, refresh the cookie:

```bash
python get_gsccca_cookie.py
```

Then update the `GSCCCA_COOKIES` GitHub Secret with the new `cookies.json` content.

**Signs the cookie is stale:**
- Scraper logs: `"Cookie captured X.X hours ago (max 12h)"`
- All counties return 0 records
- Log shows "login" in response title

---

## Manual Scrape Run

```bash
# All counties, last 3 days, both tiers
python -m scraper.fetch

# Specific counties
python -m scraper.fetch --counties "FULTON,COBB,GWINNETT" --days-back 7 --tier 1

# Output to specific file
python -m scraper.fetch --output my_leads.csv
```

---

## Instrument Tiers

| Tier | Instrument Types | Schedule |
|---|---|---|
| 1 | DEED - FORECLOSURE, DEED - FROM ESTATE, SHERIFF'S DEED, TAX SALE DEED, TRUSTEE'S DEED | Daily |
| 2 | LIEN, MATERIALMANS LIEN, NOTICE OF BANKRUPTCY, QUIT CLAIM DEED, SECURITY DEED | Weekly (Monday) |

---

## Lead Scoring (0–100)

| Condition | Points |
|---|---|
| Tier 1 instrument | +30 |
| Tier 2 instrument | +15 |
| Filed within 14 days | +20 |
| Filed within 30 days | +10 |
| Institutional grantee (bank, servicer, etc.) | +15 |
| Out-of-state grantor indicator | +15 |
| Consideration ≤ $1 or "love and affection" | +10 |

---

## Address Enrichment

Address extraction runs in two passes after scraping:

1. **GIS lookup (fast)** — `scraper/enrich.py` queries county ArcGIS REST APIs for Gwinnett and Cherokee. Matches parcel records by subdivision/lot or deed book/page.

2. **PT-61 OCR (fallback)** — `scraper/deed_ocr.py` runs for any record still missing an address. It navigates the GSCCCA book/page search, extracts the PT-61 (Georgia Real Estate Transfer Tax Return) EFLNO from `final.asp`, loads the HTML5 imaging viewer, pulls the rendered canvas as PNG, and runs pytesseract to extract the seller mailing address from Section A. Tested coverage: **~97%** across Cobb, DeKalb, and Gwinnett (remaining 3% are exempt deed types with no PT-61 filed — Trustee's Deed, Estate Deed).

---

## File Structure

```
gsccca-lead-gen/
├── scraper/
│   ├── fetch.py           # Core scraper + orchestration
│   ├── enrich.py          # Pass 1: GIS address lookup (Gwinnett, Cherokee)
│   ├── deed_ocr.py        # Pass 2: PT-61 OCR address extraction (all counties)
│   ├── score.py           # Lead scoring engine (0–100)
│   ├── counties.py        # All 159 GA counties with GSCCCA IDs
│   ├── instruments.py     # Tier 1/2 instrument definitions
│   └── requirements.txt
├── get_gsccca_cookie.py   # LOCAL ONLY — never commit output
├── upload_drive.py        # Google Drive upload
├── dashboard/
│   └── index.html         # GitHub Pages UI
├── .github/workflows/
│   └── scrape.yml
├── .gitignore
└── README.md
```

---

## Troubleshooting

**"No ViewState found"** — Cookie may be expired; refresh and retry.

**All counties return 0 results** — Check cookie age. If `_check_session_expired` logs "login in title", re-run `get_gsccca_cookie.py` and update the secret.

**Rate limited** — Increase `sleep(2-4)` delay in `fetch.py` or reduce concurrent counties.

**OCR returns no address** — Usually means the deed type has no PT-61 (Trustee's Deed, Estate Deed). Expected miss rate ~3%.

**OCR canvas is blank** — Viewport too short (must be ≥2400px) or `networkidle` didn't wait long enough for `GetImage.aspx`. Already configured in `fetch.py`.

**Drive upload fails** — Verify `GDRIVE_CREDS_JSON` secret contains valid service account JSON with Drive API access and the folder shared with the service account email.
