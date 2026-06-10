"""
Instrument type definitions with tier classification.

Numeric IDs are the actual values used in the GSCCCA Premium search dropdown
(ctl00$BodyContent$ddlInstrumentTypes). Verified from live form inspection.
"""

# Tier 1: name → numeric dropdown value
TIER_1: dict[str, str] = {
    "DEED - FORECLOSURE": "28",
    "DEED - FROM ESTATE": "27",
    "SHERIFF'S DEED":     "46",
    "TAX SALE DEED":      "47",
    "TRUSTEE'S DEED":     "50",
}

# Tier 2: name → numeric dropdown value
TIER_2: dict[str, str] = {
    "LIEN":                  "33",
    "MATERIALMANS LIEN":     "35",
    "NOTICE OF BANKRUPTCY":  "18",
    "QUIT CLAIM DEED":       "40",
    "SECURITY DEED":         "45",
}

ALL_INSTRUMENTS: dict[str, str] = {**TIER_1, **TIER_2}

# Map instrument name → tier integer for quick lookup
INSTRUMENT_TIER: dict[str, int] = {name: 1 for name in TIER_1}
INSTRUMENT_TIER.update({name: 2 for name in TIER_2})

# Reverse map: numeric ID → canonical name (for parsing results)
ID_TO_NAME: dict[str, str] = {v: k for k, v in ALL_INSTRUMENTS.items()}


def get_tier(instrument: str) -> int | None:
    """Return tier (1 or 2) for an instrument name, or None if unknown."""
    return INSTRUMENT_TIER.get(instrument.strip().upper())
