"""Title normalization for autocomplete matching."""

import re
import unicodedata


def normalize_title(title: str) -> str:
    """Normalize a product title for trigram / prefix matching.

    "Nike Air Max 90 White Sz 10" → "nike air max 90 white sz 10"
    "NIKE AIRMAX 90 BLANCO T10"   → "nike airmax 90 blanco sz 10"
    """
    s = title.lower()
    # Remove accents (é → e)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    # Collapse size variants
    s = re.sub(r"\bsize\b|\btalla\b|\bsz\b|\bt(?=\d)", "sz", s)
    # Separate numbers glued to letters (airmax90 → airmax 90)
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)
    # Keep only alphanumeric + spaces
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s
