"""immowelt.at: labelled result cards, newest first.

The ``/liste/`` route is sorted by relevance and cannot be re-sorted, so the
same 30 cards come back for days. ``/classified-search`` with
``order=DateDesc`` is what the site's own "newest" view uses.
"""

from __future__ import annotations

import re

from ..config import Search
from ..models import Listing
from .base import clean_text, denoise, jsonld_listings, parse_decimal, parse_euro

NAME = "immowelt"
HOST = "www.immowelt.at"
BASE_URL = f"https://{HOST}"

# immowelt addresses a city by an opaque location code, read off the site's
# own search box and checked against the postcodes it returns.
CITY_CODES = {
    "wien": "AD08AT2093",
    "graz": "AD06AT68",
    "linz": "AD08AT877",
    "salzburg": "AD06AT62",
}
_DEALS = {"rent": "Rent", "buy": "Buy"}
_KINDS = {"flat": "Apartment", "house": "House"}

_CARD_MARKER = 'data-testid="serp-core-classified-card-testid"'
_ID_RE = re.compile(r"/expose/([A-Za-z0-9][A-Za-z0-9-]*)")
_LINK_RE = re.compile(r'<a\b[^>]*href="([^"]*/expose/[^"]+)"[^>]*>')
_TITLE_ATTR_RE = re.compile(r'\btitle="([^"]*)"')
_ARIA_PRICE_RE = re.compile(r'aria-label="([^"]*)"[^>]*data-testid="cardmfe-price-testid"')
_TESTID_RE = re.compile(r'data-testid="[^"]*"')
_ROOMS_RE = re.compile(r"(\d{1,2}(?:[,.]\d)?)\s*Zimmer", re.IGNORECASE)
_AREA_RE = re.compile(r"(\d{1,4}(?:[,.]\d{1,2})?)\s*m²")
_POSTCODE_RE = re.compile(r"\((\d{4})\)")
# Gallery images carry "?w=<width>"; the agency logo is "?h=50" and is skipped.
_PHOTO_RE = re.compile(r"https://mms\.immowelt\.de/[0-9a-f/]+/[0-9a-f-]+\.jpg\?w=\d+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s")


class ImmoweltAdapter:
    name = NAME
    host = HOST

    def build_url(self, search: Search, kind: str) -> str:
        return (
            f"{BASE_URL}/classified-search?distributionTypes={_DEALS[search.deal]}"
            f"&estateTypes={_KINDS[kind]}&locations={CITY_CODES[search.city]}&order=DateDesc"
        )

    def parse(self, html: str, deal: str, kind: str) -> list[Listing]:
        listings = _parse_cards(html or "", deal, kind)
        if listings:
            return listings
        return jsonld_listings(html, NAME, BASE_URL, _ID_RE.pattern, deal, kind)


def _parse_cards(html: str, deal: str, kind: str) -> list[Listing]:
    html = denoise(html)
    starts = [m.start() for m in re.finditer(re.escape(_CARD_MARKER), html)]
    found: dict[str, Listing] = {}
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(html)
        listing = _parse_card(html[start:end], deal, kind)
        if listing is not None:
            found.setdefault(listing.source_id, listing)
    return list(found.values())


def _parse_card(card: str, deal: str, kind: str) -> Listing | None:
    link = _LINK_RE.search(card)
    if not link:
        return None
    url = link.group(1)
    source_id = _ID_RE.search(url).group(1)

    aria = _ARIA_PRICE_RE.search(card)
    price = parse_euro(aria.group(1)) if aria else None
    if price is None:
        price = parse_euro(_field(card, "cardmfe-price-testid"))

    facts = _field(card, "cardmfe-keyfacts-testid")
    rooms = _ROOMS_RE.search(facts)
    area = _AREA_RE.search(facts)

    address_text = _field(card, "cardmfe-description-box-address")
    postcode = _POSTCODE_RE.search(address_text)
    address = _POSTCODE_RE.sub("", address_text).strip(" ,") or None

    text = _field(card, "cardmfe-description-text-test-id")
    photos = tuple(dict.fromkeys(m.group(0) for m in _PHOTO_RE.finditer(card)))[:10]

    return Listing(
        source=NAME,
        source_id=source_id,
        url=url if url.startswith("http") else BASE_URL + url,
        title=_title(text, link.group(0)),
        deal=deal,
        kind=kind,
        price=price,
        # Result cards quote the net cold rent ("Nettokaltmiete").
        price_is_net=deal == "rent",
        size_m2=parse_decimal(area.group(1)) if area else None,
        rooms=parse_decimal(rooms.group(1)) if rooms else None,
        postcode=postcode.group(1) if postcode else None,
        address=address,
        description=text[:1000],
        photos=photos,
    )


def _field(card: str, testid: str) -> str:
    """Text of one labelled field, up to the next labelled element."""
    match = re.search(r'data-testid="' + re.escape(testid) + r'"[^>]*>', card)
    if not match:
        return ""
    following = _TESTID_RE.search(card, match.end())
    end = following.start() if following else min(len(card), match.end() + 1500)
    chunk = card[match.end() : end]
    # Drop a half tag left at the end of the slice.
    if chunk.rfind("<") > chunk.rfind(">"):
        chunk = chunk[: chunk.rfind("<")]
    return clean_text(chunk)


def _title(text: str, link_tag: str) -> str:
    """The ad's first sentence, else the card link's generic title."""
    first_sentence = _SENTENCE_END_RE.split(text, maxsplit=1)[0] if text else ""
    if sum(ch.isalpha() for ch in first_sentence) >= 10:
        return first_sentence[:200]
    attr = _TITLE_ATTR_RE.search(link_tag)
    fallback = clean_text(attr.group(1)) if attr else ""
    return (fallback or first_sentence or "Wohnung")[:200]
