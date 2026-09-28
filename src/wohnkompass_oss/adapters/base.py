"""The adapter contract and the parsing helpers every portal shares.

An adapter is two pure functions: build the search URL, and turn the returned
HTML into :class:`Listing` objects. Fetching is not the adapter's business,
so every parser can be tested offline against a saved page.
"""

from __future__ import annotations

import html as _html
import json
import re
from collections.abc import Iterator
from typing import Any, Protocol
from urllib.parse import urljoin

from ..config import Search
from ..models import Listing


class Adapter(Protocol):
    name: str
    host: str

    def build_url(self, search: Search, kind: str) -> str:
        """Search URL for the newest listings of one kind (flat or house)."""
        ...

    def parse(self, html: str, deal: str, kind: str) -> list[Listing]:
        """All listings on a search results page; [] when none are found."""
        ...


# --------------------------------------------------------------------------
# Text and number helpers
# --------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# "1.072,14" / "1 072,14" / "551.47" / "850". A dot followed by exactly three
# digits is a thousands separator; otherwise it is the decimal point.
_EURO_RE = re.compile(r"(\d{1,3}(?:[.\s  ]\d{3})+|\d+)(?:[,.](\d{1,2}))?(?!\d)")
_DECIMAL_RE = re.compile(r"\d+(?:[.,]\d+)?")
_POSTCODE_RE = re.compile(r"\b([1-9]\d{3})\b")
_VIENNA_DISTRICT_RE = re.compile(r"^1(?:0[1-9]|1\d|2[0-3])0$")


def clean_text(text: str | None) -> str:
    """Tags removed, entities decoded, whitespace collapsed."""
    if not text:
        return ""
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", text))).strip()


def parse_euro(value: Any) -> float | None:
    """Austrian money strings: '€ 1.072,14', '1.100 €', '551.47 €'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None
    match = _EURO_RE.search(str(value))
    if not match:
        return None
    whole = re.sub(r"[.\s  ]", "", match.group(1))
    fraction = match.group(2) or "0"
    amount = float(f"{whole}.{fraction}")
    return amount if amount > 0 else None


def parse_decimal(value: Any) -> float | None:
    """'71 m²' -> 71.0, '42,75' -> 42.75."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    match = _DECIMAL_RE.search(str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def postcode_from_text(*texts: str | None) -> str | None:
    """First plausible Austrian postcode, searching the texts in order.

    19xx/20xx are usually years ("Erstbezug 2026", "Baujahr 1905"), except for
    Vienna's district postcodes 1010-1230.
    """
    for text in texts:
        if not text:
            continue
        for match in _POSTCODE_RE.finditer(text):
            code = match.group(1)
            if _VIENNA_DISTRICT_RE.match(code) or not 1900 <= int(code) <= 2099:
                return code
    return None


def denoise(html: str) -> str:
    """Drop scripts, styles, SVG and comments: they hold digits but no card text."""
    html = re.sub(r"<!--.*?-->", " ", html or "", flags=re.DOTALL)
    return re.sub(
        r"<(script|style|svg|noscript)\b[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.I
    )


# --------------------------------------------------------------------------
# JSON-LD fallback
# --------------------------------------------------------------------------
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_LISTING_TYPES = {
    "offer",
    "product",
    "apartment",
    "house",
    "realestatelisting",
    "singlefamilyresidence",
    "accommodation",
    "residence",
}


def _ld_documents(html: str) -> Iterator[Any]:
    for match in _LDJSON_RE.finditer(html or ""):
        try:
            yield json.loads(match.group(1))
        except ValueError:
            continue


def _ld_nodes(node: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if depth > 8:
        return
    if isinstance(node, list):
        for item in node:
            yield from _ld_nodes(item, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if str(node.get("@type", "")).lower() in _LISTING_TYPES:
        yield node
        return
    for key in ("itemListElement", "@graph", "item", "mainEntity"):
        if key in node:
            yield from _ld_nodes(node[key], depth + 1)


def _ld_value(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) else value


def _ld_images(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, list) else [value]
    urls = []
    for item in items:
        url = item.get("url") or item.get("contentUrl") if isinstance(item, dict) else item
        if isinstance(url, str) and url.startswith("https://"):
            urls.append(url)
    return tuple(dict.fromkeys(urls))[:10]


def jsonld_listings(
    html: str, source: str, base_url: str, id_pattern: str, deal: str, kind: str
) -> list[Listing]:
    """Listings from schema.org JSON-LD, the markup portals keep for search engines."""
    out: dict[str, Listing] = {}
    for doc in _ld_documents(html):
        for node in _ld_nodes(doc):
            url, name = node.get("url") or node.get("@id"), node.get("name")
            if not isinstance(url, str) or not isinstance(name, str):
                continue
            url = urljoin(base_url + "/", url)
            id_match = re.search(id_pattern, url)
            if not id_match:
                continue
            offers = node.get("offers") if isinstance(node.get("offers"), dict) else {}
            address = node.get("address") if isinstance(node.get("address"), dict) else {}
            description = clean_text(node.get("description") or "")
            listing = Listing(
                source=source,
                source_id=id_match.group(1),
                url=url,
                title=clean_text(name)[:200],
                deal=deal,
                kind=kind,
                price=parse_euro(node.get("price") or offers.get("price")),
                size_m2=parse_decimal(_ld_value(node.get("floorSize"))),
                rooms=parse_decimal(_ld_value(node.get("numberOfRooms"))),
                postcode=address.get("postalCode") or postcode_from_text(name, description),
                address=clean_text(address.get("streetAddress") or "") or None,
                description=description[:1000],
                photos=_ld_images(node.get("image")),
            )
            out.setdefault(listing.source_id, listing)
    return list(out.values())
