"""Reject listings that cannot be real before they reach the user.

Wrong data is worse than no data: a parser that picks up the wrong number
(a phone number as the price, a year as the postcode) must not produce an
alert that looks trustworthy.
"""

from __future__ import annotations

from .models import Listing

RENT_BOUNDS = (50.0, 25_000.0)
BUY_BOUNDS = (10_000.0, 25_000_000.0)
SIZE_BOUNDS = (5.0, 2_000.0)
RENT_PER_M2_BOUNDS = (3.0, 150.0)


def implausible(listing: Listing) -> str | None:
    """Why the listing cannot be real, or None when it looks sane."""
    code = listing.postcode
    if code is not None and not (len(code) == 4 and code.isdigit() and 1000 <= int(code) <= 9999):
        return f"postcode {code!r} is not Austrian"

    price = listing.effective_price
    if price is not None:
        low, high = BUY_BOUNDS if listing.is_buy else RENT_BOUNDS
        if not low <= price <= high:
            label = "purchase price" if listing.is_buy else "rent"
            return f"{label} {price:.0f} € outside [{low:.0f}, {high:.0f}]"

    size = listing.size_m2
    if size is not None and not SIZE_BOUNDS[0] <= size <= SIZE_BOUNDS[1]:
        return f"size {size:g} m² implausible"

    per_m2 = listing.price_per_m2
    if not listing.is_buy and per_m2 is not None:
        low, high = RENT_PER_M2_BOUNDS
        if not low <= per_m2 <= high:
            return f"rent {per_m2:.1f} €/m² outside [{low:g}, {high:g}]"
    return None
