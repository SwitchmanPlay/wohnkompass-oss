"""User-facing text in English and German. Both languages must have the same keys."""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "alert_new": "🏠 New",
        "alert_drop": "📉 Price drop",
        "was": "was {price}",
        "also_on": "also on {portals}",
        "net": "net {price}",
        "rooms": "{rooms} rooms",
        "price_unknown": "price on request",
        "flag_befristet": "fixed-term lease",
        "flag_abloese": "key money (Ablöse)",
        "flag_provision": "agent commission",
        "flag_wohnticket": "needs a Wohn-Ticket",
        "button_open": "Open listing",
        "button_draft": "✍️ Draft letter",
        "portal_paused": "⏸ {portal} refused our request ({reason}). Paused until {until}.",
        "parser_broken": (
            "⚠️ {portal} returned no listings {count} times in a row. The page layout may "
            "have changed; check for an update of wohnkompass-oss."
        ),
        "start": (
            "👋 wohnkompass-oss is watching {portals} for you.\n"
            "Search: {search}\nChecking every ~{minutes} min.\n\n"
            "/status · /check · /pause · /resume"
        ),
        "status_header": "📊 Status",
        "status_portal_ok": "✅ {portal}: last check {when}, {count} listings",
        "status_portal_never": "⏳ {portal}: not checked yet",
        "status_portal_paused": "⏸ {portal}: paused until {until} ({reason})",
        "status_pool": "Listings remembered: {count}",
        "status_paused": "Watching is paused. /resume to continue.",
        "paused": "⏸ Paused. No checks until /resume.",
        "resumed": "▶️ Watching again.",
        "check_started": "🔎 Checking now…",
        "check_done": "Done: {fetched} listings checked, {alerts} new matches.",
        "check_busy": "A check is already running.",
        "letter_working": "✍️ Writing a draft…",
        "letter_failed": "Sorry, the letter could not be written. Try again in a minute.",
        "letter_gone": "This listing is no longer in the database.",
        "search_rent": "rent",
        "search_buy": "buy",
    },
    "de": {
        "alert_new": "🏠 Neu",
        "alert_drop": "📉 Preis gesenkt",
        "was": "vorher {price}",
        "also_on": "auch auf {portals}",
        "net": "netto {price}",
        "rooms": "{rooms} Zimmer",
        "price_unknown": "Preis auf Anfrage",
        "flag_befristet": "befristeter Mietvertrag",
        "flag_abloese": "Ablöse",
        "flag_provision": "Provision",
        "flag_wohnticket": "Wohn-Ticket nötig",
        "button_open": "Inserat öffnen",
        "button_draft": "✍️ Anschreiben",
        "portal_paused": ("⏸ {portal} hat die Anfrage abgelehnt ({reason}). Pausiert bis {until}."),
        "parser_broken": (
            "⚠️ {portal} hat {count}-mal hintereinander keine Inserate geliefert. Vielleicht "
            "hat sich die Seite geändert; prüfe, ob es ein Update von wohnkompass-oss gibt."
        ),
        "start": (
            "👋 wohnkompass-oss beobachtet {portals} für dich.\n"
            "Suche: {search}\nPrüfung etwa alle {minutes} Min.\n\n"
            "/status · /check · /pause · /resume"
        ),
        "status_header": "📊 Status",
        "status_portal_ok": "✅ {portal}: zuletzt {when}, {count} Inserate",
        "status_portal_never": "⏳ {portal}: noch nicht geprüft",
        "status_portal_paused": "⏸ {portal}: pausiert bis {until} ({reason})",
        "status_pool": "Gespeicherte Inserate: {count}",
        "status_paused": "Die Suche ist pausiert. /resume zum Fortsetzen.",
        "paused": "⏸ Pausiert. Keine Prüfungen bis /resume.",
        "resumed": "▶️ Die Suche läuft wieder.",
        "check_started": "🔎 Prüfe jetzt…",
        "check_done": "Fertig: {fetched} Inserate geprüft, {alerts} neue Treffer.",
        "check_busy": "Es läuft bereits eine Prüfung.",
        "letter_working": "✍️ Schreibe einen Entwurf…",
        "letter_failed": "Das Anschreiben konnte nicht erstellt werden. Bitte gleich nochmal.",
        "letter_gone": "Dieses Inserat ist nicht mehr gespeichert.",
        "search_rent": "Miete",
        "search_buy": "Kauf",
    },
}

PORTAL_NAMES = {"immowelt": "immowelt", "derstandard": "derStandard"}


def t(language: str, key: str, **values: object) -> str:
    return STRINGS[language][key].format(**values)
