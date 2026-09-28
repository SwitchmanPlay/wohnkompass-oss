"""Rule filters and red flags. Pure functions, no AI involved.

Missing data passes a filter (a card without a room count may still be the
right flat), with one exception: when a budget is set, a listing without a
price fails, because "price on request" is rarely good news.
"""

from __future__ import annotations

import re

from .config import Search
from .models import Listing

_FLAG_PATTERNS: dict[str, tuple[re.Pattern[str], re.Pattern[str] | None]] = {
    # Fixed-term lease ("befristet", "Befristung"), but not "unbefristet".
    "befristet": (
        re.compile(r"(?<!un)befrist|fixed[- ]term", re.I),
        None,
    ),
    # Key money for furniture or a kitchen.
    "abloese": (
        re.compile(r"abl(?:ö|oe)se(?!frei)|key money", re.I),
        re.compile(r"(?:keine|ohne|no)\s+abl(?:ö|oe)se|key money:?\s*none", re.I),
    ),
    # Agent commission charged to the tenant or buyer.
    "provision": (
        re.compile(r"provision(?!sfrei)|maklergebühr|commission", re.I),
        re.compile(r"provisionsfrei|(?:keine|ohne)\s+(?:makler)?provision|no\s+commission", re.I),
    ),
    # Vienna's council and subsidised flats need a "Wohn-Ticket".
    "wohnticket": (
        re.compile(r"wohn-?ticket|gemeindewohnung|gemeindebau|vormerkschein", re.I),
        None,
    ),
}


def red_flags(listing: Listing) -> list[str]:
    """Keys of the catches found in the listing text, in a fixed order."""
    text = f"{listing.title}\n{listing.description}"
    flags = []
    for key, (pattern, negation) in _FLAG_PATTERNS.items():
        if key == "befristet" and listing.is_buy:
            continue
        if negation is not None and negation.search(text):
            continue
        if pattern.search(text):
            flags.append(key)
    return flags


def matches(listing: Listing, search: Search) -> bool:
    if listing.deal != search.deal or listing.kind not in search.types:
        return False
    if search.postcodes and listing.postcode not in search.postcodes:
        return False

    price = listing.effective_price
    if search.price_min is not None or search.price_max is not None:
        if price is None:
            return False
        if search.price_min is not None and price < search.price_min:
            return False
        if search.price_max is not None and price > search.price_max:
            return False

    if _below(listing.size_m2, search.size_min) or _below(listing.rooms, search.rooms_min):
        return False
    return not set(red_flags(listing)) & set(search.exclude_red_flags)


def _below(value: float | None, minimum: float | None) -> bool:
    """True only when both are known and the value is too small."""
    return value is not None and minimum is not None and value < minimum
