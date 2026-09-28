"""immobilien.derstandard.at: one ``<li class="sc-listing-card">`` per result.

New-build projects appear as a single card linking to ``/gruppe/<id>`` with
several units inside; those are skipped, individual listings are kept.
"""

from __future__ import annotations

import re

from ..config import Search
from ..models import Listing
from .base import clean_text, denoise, jsonld_listings, parse_decimal, parse_euro

NAME = "derstandard"
HOST = "immobilien.derstandard.at"
BASE_URL = f"https://{HOST}"

CITY_SLUGS = {
    "wien": "wien",
    "graz": "graz",
    "linz": "linz",
    "salzburg": "salzburg-stadt",
}
_DEALS = {"rent": "mieten", "buy": "kaufen"}
_KINDS = {"flat": "wohnung", "house": "haus"}

_CARD_SPLIT_RE = re.compile(r'(?=<li\s+class="sc-listing-card[\s"])')
_ID_RE = re.compile(r'href="(?:https://immobilien\.derstandard\.at)?/detail/(\d+)"')
_ZIP_CITY_RE = re.compile(r'class="property-address-zip-city"[^>]*>(.*?)</span>', re.S)
_STREET_RE = re.compile(r'class="property-address-rest"[^>]*>(.*?)</span>', re.S)
_PRICE_RE = re.compile(r'class="[^"]*propertyPrice[^"]*"[^>]*>(.*?)</span>', re.S)
_TITLE_RE = re.compile(r'<div[^>]*class="sc-listing-card-title[^"]*"[^>]*>(.*?)</div>', re.S)
_FOOTER_RE = re.compile(r'class="sc-listing-card-footer(?:\s[^"]*)?"')
_MAIN_PRICE_RE = re.compile(r'class="[^"]*sc-listing-card-footer-item-main[^"]*"[^>]*>(.*?)</div>')
_AREA_RE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*m²")
_ROOMS_RE = re.compile(r"(\d{1,2}(?:[.,]5)?)\s*Zimmer", re.IGNORECASE)
_SOURCE_RE = re.compile(r'(?:srcSet|srcset|src)="([^"]+)"')


class DerStandardAdapter:
    name = NAME
    host = HOST

    def build_url(self, search: Search, kind: str) -> str:
        slug = CITY_SLUGS[search.city]
        return f"{BASE_URL}/suche/{slug}/{_DEALS[search.deal]}-{_KINDS[kind]}"

    def parse(self, html: str, deal: str, kind: str) -> list[Listing]:
        listings = _parse_cards(html or "", deal, kind)
        if listings:
            return listings
        return jsonld_listings(html, NAME, BASE_URL, r"/detail/(\d+)", deal, kind)


def _parse_cards(html: str, deal: str, kind: str) -> list[Listing]:
    found: dict[str, Listing] = {}
    for chunk in _CARD_SPLIT_RE.split(denoise(html))[1:]:
        card = chunk.split("</li>", 1)[0] if "</li>" in chunk else chunk
        listing = _parse_card(card, deal, kind)
        if listing is not None:
            found.setdefault(listing.source_id, listing)
    return list(found.values())


def _parse_card(card: str, deal: str, kind: str) -> Listing | None:
    if "/gruppe/" in card:
        return None
    id_match = _ID_RE.search(card)
    if not id_match:
        return None
    source_id = id_match.group(1)

    zip_city = _clean_match(_ZIP_CITY_RE, card).strip(" ,")
    street = _clean_match(_STREET_RE, card).strip(" ,")
    postcode = re.match(r"(\d{4})\b", zip_city)
    address = ", ".join(part for part in (street, zip_city) if part) or None

    footer_at = _FOOTER_RE.search(card)
    footer = card[footer_at.start() :] if footer_at else ""
    footer_text = clean_text(footer)
    area = _AREA_RE.search(footer_text)
    rooms = _ROOMS_RE.search(footer_text)
    price_text = _clean_match(_PRICE_RE, card) or _clean_match(_MAIN_PRICE_RE, footer)

    title = _clean_match(_TITLE_RE, card) or zip_city or "Wohnung"
    return Listing(
        source=NAME,
        source_id=source_id,
        url=f"{BASE_URL}/detail/{source_id}",
        title=title[:200],
        deal=deal,
        kind=kind,
        price=parse_euro(price_text),
        size_m2=parse_decimal(area.group(1)) if area else None,
        rooms=parse_decimal(rooms.group(1)) if rooms else None,
        postcode=postcode.group(1) if postcode else None,
        address=address,
        photos=_photos(card),
    )


def _clean_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return clean_text(match.group(1)) if match else ""


def _photos(card: str) -> tuple[str, ...]:
    """One JPEG per gallery slide. The agency logo sits outside the slider."""
    photos: list[str] = []
    for slide in card.split('class="swiper-slide"')[1:]:
        slide = slide.split("</picture>", 1)[0]
        for match in _SOURCE_RE.finditer(slide):
            url = match.group(1).split(",")[0].split()[0]
            if "format:jpg" in url and url.startswith("https://"):
                photos.append(url)
                break
    return tuple(dict.fromkeys(photos))[:10]
