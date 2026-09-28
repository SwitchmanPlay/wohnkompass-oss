"""Cross-portal near-duplicate detection.

Agencies post the same flat on several portals. Two listings from different
portals are the same flat when their titles are near-identical, or when they
share a postcode and their price (±3 %) and size (±2 m²) agree.
"""

from __future__ import annotations

import re

from .models import Listing

PRICE_TOLERANCE_PCT = 3.0
SIZE_TOLERANCE_M2 = 2.0

_WORD_RE = re.compile(r"[a-z0-9]+")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def normalize_title(title: str | None) -> str:
    """Sorted, lowercased word set: case, punctuation, order and umlaut spelling vanish."""
    words = _WORD_RE.findall((title or "").lower().translate(_UMLAUTS))
    return " ".join(sorted(set(words)))


def titles_near_identical(a: str | None, b: str | None) -> bool:
    """The shorter word set is contained in the longer one and is substantial."""
    na, nb = normalize_title(a), normalize_title(b)
    shorter, longer = sorted((na, nb), key=len)
    if len(shorter) < 20 or len(shorter.split()) < 3:
        return False  # "Wohnung" alone must never merge two flats
    return set(shorter.split()) <= set(longer.split())


def is_duplicate(a: Listing, b: Listing) -> bool:
    if a.source == b.source:
        return False
    if titles_near_identical(a.title, b.title):
        return True
    if not a.postcode or a.postcode != b.postcode:
        return False
    pa, pb = a.effective_price, b.effective_price
    if not pa or not pb or a.size_m2 is None or b.size_m2 is None:
        return False
    if abs(pa - pb) > PRICE_TOLERANCE_PCT / 100 * max(pa, pb):
        return False
    return abs(a.size_m2 - b.size_m2) <= SIZE_TOLERANCE_M2
