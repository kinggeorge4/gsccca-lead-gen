"""Instrument type definitions with tier classification."""

TIER_1 = [
    "DEED - FORECLOSURE",
    "DEED - FROM ESTATE",
    "SHERIFF'S DEED",
    "TAX SALE DEED",
    "TRUSTEE'S DEED",
]

TIER_2 = [
    "LIEN",
    "MATERIALMANS LIEN",
    "NOTICE OF BANKRUPTCY",
    "QUIT CLAIM DEED",
    "SECURITY DEED",
]

ALL_INSTRUMENTS = TIER_1 + TIER_2

# Map instrument name → tier integer for quick lookup
INSTRUMENT_TIER: dict[str, int] = {name: 1 for name in TIER_1}
INSTRUMENT_TIER.update({name: 2 for name in TIER_2})

# GSCCCA dropdown values may differ slightly from display names.
# This map normalizes what the site returns to our canonical names.
# Populated from observed responses — extend as new variants appear.
GSCCCA_NAME_MAP: dict[str, str] = {
    "DEED-FORECLOSURE": "DEED - FORECLOSURE",
    "DEED-FROM ESTATE": "DEED - FROM ESTATE",
    "SHERIFFS DEED": "SHERIFF'S DEED",
    "SHERIFF DEED": "SHERIFF'S DEED",
    "TAX DEED": "TAX SALE DEED",
    "TRUSTEES DEED": "TRUSTEE'S DEED",
    "TRUSTEE DEED": "TRUSTEE'S DEED",
    "MATERIALMAN LIEN": "MATERIALMANS LIEN",
    "MATERIALMAN'S LIEN": "MATERIALMANS LIEN",
    "BANKRUPTCY": "NOTICE OF BANKRUPTCY",
    "QUITCLAIM DEED": "QUIT CLAIM DEED",
    "QUIT-CLAIM DEED": "QUIT CLAIM DEED",
    "SECURITY DEED": "SECURITY DEED",
}


def normalize_instrument(raw: str) -> str:
    """Return canonical instrument name from raw GSCCCA string."""
    cleaned = raw.strip().upper()
    return GSCCCA_NAME_MAP.get(cleaned, cleaned)


def get_tier(instrument: str) -> int | None:
    """Return tier (1 or 2) for a normalized instrument name, or None if unknown."""
    return INSTRUMENT_TIER.get(normalize_instrument(instrument))
