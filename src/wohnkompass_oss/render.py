"""Telegram messages (HTML parse mode). Listing text is always escaped."""

from __future__ import annotations

import time
from collections.abc import Iterable
from html import escape

from .ai import Score
from .config import Search
from .filters import red_flags
from .i18n import PORTAL_NAMES, t
from .models import Listing
from .store import PortalState


def format_eur(value: float | None, language: str) -> str | None:
    if value is None:
        return None
    if language == "de":
        return f"{value:,.0f} €".replace(",", ".")
    return f"€{value:,.0f}"


def format_time(timestamp: float) -> str:
    return time.strftime("%d.%m. %H:%M", time.localtime(timestamp))


def portal_name(source: str) -> str:
    return PORTAL_NAMES.get(source, source)


def _number(value: float) -> str:
    return f"{value:g}"


def render_alert(
    listing: Listing,
    language: str,
    score: Score | None = None,
    also_on: Iterable[str] = (),
    previous_price: float | None = None,
) -> str:
    lang = language
    header = t(lang, "alert_drop" if previous_price else "alert_new")
    lines = [f'{header} · <b><a href="{escape(listing.url)}">{escape(listing.title)}</a></b>']

    source_line = portal_name(listing.source)
    others = [portal_name(s) for s in also_on]
    if others:
        source_line += " · " + t(lang, "also_on", portals=", ".join(others))
    lines.append(f"<i>{escape(source_line)}</i>")

    facts = []
    price = format_eur(listing.effective_price, lang)
    if price is None:
        facts.append(t(lang, "price_unknown"))
    elif listing.price_is_estimate and listing.size_m2:
        net = format_eur(listing.price, lang)
        facts.append(f"≈ {price} ({t(lang, 'net', price=net)})")
    elif listing.price_is_estimate:
        facts.append(t(lang, "net", price=price))
    else:
        facts.append(price)
    if listing.size_m2:
        facts.append(f"{_number(listing.size_m2)} m²")
    if listing.rooms:
        facts.append(t(lang, "rooms", rooms=_number(listing.rooms)))
    place = listing.postcode or ""
    if listing.address and listing.address not in place:
        place = f"{place} {listing.address}".strip()
    if place:
        facts.append(escape(place))
    lines.append("💶 " + " · ".join(facts))

    if previous_price:
        lines.append("📉 " + t(lang, "was", price=format_eur(previous_price, lang)))
    flags = red_flags(listing)
    if flags:
        lines.append("⚠️ " + ", ".join(t(lang, f"flag_{flag}") for flag in flags))
    if score is not None:
        lines.append(f"🤖 <b>{score.score}/10</b> · {escape(score.reason)}")
    return "\n".join(lines)


def render_search(search: Search, language: str) -> str:
    parts = [
        f"{search.city.capitalize()} · {t(language, 'search_' + search.deal)}",
        "/".join(search.types),
    ]
    if search.price_max:
        low = format_eur(search.price_min, language) if search.price_min else None
        high = format_eur(search.price_max, language)
        parts.append(f"{low}–{high}" if low else f"≤ {high}")
    if search.size_min:
        parts.append(f"≥ {_number(search.size_min)} m²")
    if search.rooms_min:
        parts.append("≥ " + t(language, "rooms", rooms=_number(search.rooms_min)))
    if search.postcodes:
        parts.append(", ".join(search.postcodes))
    return " · ".join(parts)


def render_status(
    language: str,
    portals: Iterable[tuple[str, PortalState]],
    pool_size: int,
    paused: bool,
    now: float | None = None,
) -> str:
    now = time.time() if now is None else now
    lines = [f"<b>{t(language, 'status_header')}</b>"]
    for source, state in portals:
        name = portal_name(source)
        if state.paused_until and state.paused_until > now:
            lines.append(
                t(
                    language,
                    "status_portal_paused",
                    portal=name,
                    until=format_time(state.paused_until),
                    reason=escape(state.reason),
                )
            )
        elif state.last_run is None:
            lines.append(t(language, "status_portal_never", portal=name))
        else:
            lines.append(
                t(
                    language,
                    "status_portal_ok",
                    portal=name,
                    when=format_time(state.last_run),
                    count=state.last_count or 0,
                )
            )
    lines.append(t(language, "status_pool", count=pool_size))
    if paused:
        lines.append(t(language, "status_paused"))
    return "\n".join(lines)
