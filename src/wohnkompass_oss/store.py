"""SQLite storage: the listing pool and per-portal state.

The pool remembers every listing seen (not only the ones sent), so a flat
that was too expensive yesterday can alert today when its price drops.
Portal pauses live here too, so a restart never hammers a portal that just
blocked us.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .models import Listing

Change = Literal["new", "seen", "price_drop"]

# Portals round prices; a drop has to be at least this big to count.
MIN_PRICE_DROP_EUR = 1.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    data          TEXT NOT NULL,
    price         REAL,
    prev_price    REAL,
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL,
    notified_at   REAL,
    send_failures INTEGER NOT NULL DEFAULT 0,
    also_on       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS listings_source ON listings (source);
CREATE TABLE IF NOT EXISTS portals (
    source        TEXT PRIMARY KEY,
    paused_until  REAL,
    strikes       INTEGER NOT NULL DEFAULT 0,
    reason        TEXT NOT NULL DEFAULT '',
    empty_streak  INTEGER NOT NULL DEFAULT 0,
    last_run      REAL,
    last_count    INTEGER
);
"""


@dataclass(frozen=True)
class PortalState:
    paused_until: float | None = None
    strikes: int = 0
    reason: str = ""
    empty_streak: int = 0
    last_run: float | None = None
    last_count: int | None = None


class Store:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------ pool
    def upsert(self, listing: Listing, now: float) -> Change:
        data = json.dumps(asdict(listing), ensure_ascii=False)
        price = listing.effective_price
        row = self._db.execute("SELECT price FROM listings WHERE id = ?", (listing.id,)).fetchone()
        with self._db:
            if row is None:
                self._db.execute(
                    "INSERT INTO listings (id, source, data, price, first_seen, last_seen)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (listing.id, listing.source, data, price, now, now),
                )
                return "new"
            old = row["price"]
            dropped = old is not None and price is not None and price <= old - MIN_PRICE_DROP_EUR
            self._db.execute(
                "UPDATE listings SET data = ?, price = ?, last_seen = ?,"
                " prev_price = CASE WHEN ? THEN price ELSE prev_price END WHERE id = ?",
                (data, price, now, dropped, listing.id),
            )
        return "price_drop" if dropped else "seen"

    def get(self, listing_id: str) -> Listing | None:
        row = self._db.execute("SELECT data FROM listings WHERE id = ?", (listing_id,)).fetchone()
        return _to_listing(row["data"]) if row else None

    def previous_price(self, listing_id: str) -> float | None:
        row = self._db.execute(
            "SELECT prev_price FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return row["prev_price"] if row else None

    def has_rows(self, source: str) -> bool:
        row = self._db.execute("SELECT 1 FROM listings WHERE source = ? LIMIT 1", (source,))
        return row.fetchone() is not None

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    def prune(self, now: float, days: int = 30) -> int:
        with self._db:
            cursor = self._db.execute(
                "DELETE FROM listings WHERE last_seen < ?", (now - days * 86_400,)
            )
        return cursor.rowcount

    # ---------------------------------------------------------- notifications
    def was_notified(self, listing_id: str) -> bool:
        row = self._db.execute(
            "SELECT notified_at FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return bool(row and row["notified_at"] is not None)

    def mark_notified(self, listing_id: str, now: float) -> None:
        with self._db:
            self._db.execute("UPDATE listings SET notified_at = ? WHERE id = ?", (now, listing_id))

    def record_send_failure(self, listing_id: str) -> int:
        with self._db:
            self._db.execute(
                "UPDATE listings SET send_failures = send_failures + 1 WHERE id = ?",
                (listing_id,),
            )
        row = self._db.execute(
            "SELECT send_failures FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return row["send_failures"] if row else 0

    def pending_retries(self, max_failures: int) -> list[Listing]:
        """Listings whose alert failed to send and may be tried again."""
        rows = self._db.execute(
            "SELECT data FROM listings WHERE notified_at IS NULL"
            " AND send_failures BETWEEN 1 AND ? ORDER BY first_seen",
            (max_failures - 1,),
        )
        return [_to_listing(row["data"]) for row in rows]

    def notified_listings(self, exclude_source: str) -> list[Listing]:
        rows = self._db.execute(
            "SELECT data FROM listings WHERE notified_at IS NOT NULL AND source != ?",
            (exclude_source,),
        )
        return [_to_listing(row["data"]) for row in rows]

    def add_also_on(self, listing_id: str, source: str) -> None:
        sources = self.also_on(listing_id)
        if source in sources:
            return
        with self._db:
            self._db.execute(
                "UPDATE listings SET also_on = ? WHERE id = ?",
                (",".join([*sources, source]), listing_id),
            )

    def also_on(self, listing_id: str) -> list[str]:
        row = self._db.execute(
            "SELECT also_on FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return [s for s in (row["also_on"] if row else "").split(",") if s]

    # ---------------------------------------------------------- portal state
    def portal_state(self, source: str) -> PortalState:
        row = self._db.execute("SELECT * FROM portals WHERE source = ?", (source,)).fetchone()
        if row is None:
            return PortalState()
        return PortalState(
            paused_until=row["paused_until"],
            strikes=row["strikes"],
            reason=row["reason"],
            empty_streak=row["empty_streak"],
            last_run=row["last_run"],
            last_count=row["last_count"],
        )

    def set_pause(self, source: str, until: float, strikes: int, reason: str = "") -> None:
        self._update_portal(source, paused_until=until, strikes=strikes, reason=reason)

    def clear_pause(self, source: str) -> None:
        self._update_portal(source, paused_until=None, strikes=0, reason="")

    def record_empty(self, source: str) -> int:
        streak = self.portal_state(source).empty_streak + 1
        self._update_portal(source, empty_streak=streak)
        return streak

    def reset_empty(self, source: str) -> None:
        self._update_portal(source, empty_streak=0)

    def set_last_run(self, source: str, now: float, count: int) -> None:
        self._update_portal(source, last_run=now, last_count=count)

    def _update_portal(self, source: str, **fields: object) -> None:
        columns = ", ".join(f"{key} = ?" for key in fields)
        with self._db:
            self._db.execute("INSERT OR IGNORE INTO portals (source) VALUES (?)", (source,))
            self._db.execute(
                f"UPDATE portals SET {columns} WHERE source = ?", (*fields.values(), source)
            )


def _to_listing(data: str) -> Listing:
    fields = json.loads(data)
    fields["photos"] = tuple(fields.get("photos") or ())
    return Listing(**fields)
