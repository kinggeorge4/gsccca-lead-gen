"""Lead scoring engine — returns integer 0-100."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .instruments import get_tier

INSTITUTIONAL_KEYWORDS = re.compile(
    r"\b(BANK|TRUST|NATIONAL\s+ASSOC|MORTGAGE|FINANCIAL|SERVICER|N\.A\.|CORP|"
    r"FEDERAL|SAVINGS|CREDIT\s+UNION|HOME\s+LOAN|FANNIE|FREDDIE|FHA|HUD)\b",
    re.I,
)

LOW_CONSIDERATION = re.compile(
    r"^\$?0*\.?0*$|^\$1\.?0*$|\blove\s+and\s+affection\b|\bno\s+consideration\b|^\$0+\.00$",
    re.I,
)

OUT_OF_STATE_INDICATORS = re.compile(
    r"\b(AL|FL|SC|NC|TN|TX|NY|CA|OH|MI|IL|PA|VA|MD|NJ|MA|CO|WA|AZ|NV|"
    r"ALABAMA|FLORIDA|SOUTH\s+CAROLINA|NORTH\s+CAROLINA|TENNESSEE|TEXAS)\b",
    re.I,
)


def _parse_file_date(date_str: str) -> datetime | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def score_lead(lead: dict) -> int:
    score = 0

    # Tier points
    tier = lead.get("tier") or get_tier(lead.get("instrument_type", ""))
    if tier == 1:
        score += 30
    elif tier == 2:
        score += 15

    # Recency points
    parsed_date = _parse_file_date(lead.get("file_date", ""))
    if parsed_date:
        age = datetime.now() - parsed_date
        if age <= timedelta(days=14):
            score += 20
        elif age <= timedelta(days=30):
            score += 10

    # Institutional grantee
    grantee = lead.get("grantee_name", "")
    grantor = lead.get("grantor_name", "")
    if INSTITUTIONAL_KEYWORDS.search(grantee):
        score += 15
    elif "LLC" in grantee.upper() and _looks_like_individual(grantor):
        score += 15

    # Out-of-state grantor indicator
    if OUT_OF_STATE_INDICATORS.search(grantor):
        score += 15

    # Low / no consideration amount
    consideration = lead.get("consideration_amount", "").strip()
    if consideration and LOW_CONSIDERATION.search(consideration):
        score += 10

    return min(score, 100)


def _looks_like_individual(name: str) -> bool:
    """Heuristic: name looks like a person (not a company) if it has no corporate suffixes."""
    corporate = re.compile(
        r"\b(LLC|INC|CORP|LTD|LP|LLP|TRUST|BANK|ASSOCIATION|ASSOC|CO\.?)\b", re.I
    )
    return not corporate.search(name) and len(name.split()) >= 2
