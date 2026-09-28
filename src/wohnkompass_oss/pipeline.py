"""One poll cycle: fetch → parse → check → filter → dedup → score → notify.

The pipeline knows nothing about Telegram; it talks to a :class:`Notifier`.
That keeps it testable and lets ``--dry-run`` print to the console instead.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from html import unescape
from typing import Protocol

from .adapters import Adapter, get_adapter
from .ai import AIClient, Score
from .config import Settings
from .dedup import is_duplicate
from .filters import matches
from .http import Blocked, FetchError, pause_seconds
from .i18n import t
from .models import Listing
from .plausibility import implausible
from .render import format_time, portal_name, render_alert
from .store import Change, Store

log = logging.getLogger(__name__)

EMPTY_ALERT_AFTER = 3
MAX_SEND_ATTEMPTS = 3
POOL_RETENTION_DAYS = 30


class Notifier(Protocol):
    async def send_alert(self, listing: Listing, text: str, can_draft: bool) -> None: ...

    async def send_text(self, text: str) -> None: ...


class Fetcher(Protocol):
    async def get(self, url: str) -> str: ...


class Scorer(Protocol):
    async def score(
        self, listing: Listing, search: object, language: str, photos: bool
    ) -> Score | None: ...


@dataclass
class CycleReport:
    fetched: int = 0
    new: int = 0
    alerts: int = 0
    blocked: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ConsoleNotifier:
    """Prints alerts instead of sending them (``wohnkompass-oss once --dry-run``)."""

    async def send_alert(self, listing: Listing, text: str, can_draft: bool) -> None:
        print(_plain(text), end="\n\n", flush=True)

    async def send_text(self, text: str) -> None:
        print(_plain(text), end="\n\n", flush=True)


def _plain(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


async def run_cycle(
    settings: Settings,
    store: Store,
    client: Fetcher,
    notifier: Notifier,
    adapters: Mapping[str, Adapter] | None = None,
    ai: Scorer | AIClient | None = None,
    now: float | None = None,
    alert_on_first_run: bool = False,
) -> CycleReport:
    """Check every enabled portal once.

    The first successful fetch of a portal only fills the pool, so starting the
    bot does not flood the chat with every listing currently online. Dry runs
    pass ``alert_on_first_run=True`` to show what would match right now.
    """
    now = time.time() if now is None else now
    adapters = adapters or {name: get_adapter(name) for name in settings.portals}
    ai = ai if settings.ai.enabled else None
    report = CycleReport()
    context = _Cycle(settings, store, notifier, ai, now, report, alert_on_first_run)

    for listing in store.pending_retries(MAX_SEND_ATTEMPTS):
        await context.deliver(listing, score=None, previous_price=None)

    for name in settings.portals:
        await context.run_portal(name, adapters[name], client)

    store.prune(now, POOL_RETENTION_DAYS)
    log.info(
        "cycle done: %d checked, %d new, %d alerts, blocked=%s, errors=%s",
        report.fetched,
        report.new,
        report.alerts,
        report.blocked,
        report.errors,
    )
    return report


@dataclass
class _Cycle:
    settings: Settings
    store: Store
    notifier: Notifier
    ai: Scorer | None
    now: float
    report: CycleReport
    alert_on_first_run: bool = False

    @property
    def lang(self) -> str:
        return self.settings.language

    async def run_portal(self, name: str, adapter: Adapter, client: Fetcher) -> None:
        store, search = self.store, self.settings.search
        state = store.portal_state(name)
        if state.paused_until and state.paused_until > self.now:
            self.report.skipped.append(name)
            return

        first_run = not store.has_rows(name) and not self.alert_on_first_run
        listings: list[Listing] = []
        try:
            for kind in search.types:
                html = await client.get(adapter.build_url(search, kind))
                listings += adapter.parse(html, search.deal, kind)
        except Blocked as blocked:
            await self._pause(name, state.strikes + 1, blocked)
            return
        except FetchError as error:
            log.warning("%s: %s", name, error)
            self.report.errors.append(f"{name}: {error}")
            return

        if state.strikes:
            store.clear_pause(name)
        store.set_last_run(name, self.now, len(listings))
        if not listings:
            streak = store.record_empty(name)
            log.warning("%s: no listings on the results page (%d in a row)", name, streak)
            if streak == EMPTY_ALERT_AFTER:
                await self._text(
                    t(self.lang, "parser_broken", portal=portal_name(name), count=streak)
                )
            return
        store.reset_empty(name)

        for listing in listings:
            reason = implausible(listing)
            if reason:
                log.info("%s: dropped %s (%s)", name, listing.id, reason)
                continue
            change = store.upsert(listing, self.now)
            self.report.fetched += 1
            if change == "new":
                self.report.new += 1
            if not first_run:
                await self._consider(listing, change)

    async def _consider(self, listing: Listing, change: Change) -> None:
        store = self.store
        if change == "seen" or store.was_notified(listing.id):
            return
        if not matches(listing, self.settings.search):
            return
        for other in store.notified_listings(exclude_source=listing.source):
            if is_duplicate(listing, other):
                log.info("%s is a duplicate of %s", listing.id, other.id)
                store.add_also_on(other.id, listing.source)
                store.mark_notified(listing.id, self.now)
                return

        score = None
        if self.ai is not None:
            score = await self.ai.score(
                listing, self.settings.search, self.lang, self.settings.ai.photos
            )
            if score is not None and score.score < self.settings.ai.min_score:
                log.info("%s scored %d/10, below the minimum", listing.id, score.score)
                return
        previous = store.previous_price(listing.id) if change == "price_drop" else None
        await self.deliver(listing, score, previous)

    async def deliver(
        self, listing: Listing, score: Score | None, previous_price: float | None
    ) -> None:
        text = render_alert(
            listing,
            self.lang,
            score=score,
            also_on=self.store.also_on(listing.id),
            previous_price=previous_price,
        )
        try:
            await self.notifier.send_alert(listing, text, can_draft=self.ai is not None)
        except Exception as error:
            attempts = self.store.record_send_failure(listing.id)
            log.warning(
                "sending %s failed (%d/%d): %s", listing.id, attempts, MAX_SEND_ATTEMPTS, error
            )
            return
        self.store.mark_notified(listing.id, self.now)
        self.report.alerts += 1

    async def _pause(self, name: str, strikes: int, blocked: Blocked) -> None:
        wait = max(pause_seconds(strikes), blocked.retry_after or 0)
        until = self.now + wait
        self.store.set_pause(name, until=until, strikes=strikes, reason=blocked.reason)
        self.report.blocked.append(name)
        log.warning("%s blocked us (%s); paused for %.0f min", name, blocked.reason, wait / 60)
        await self._text(
            t(
                self.lang,
                "portal_paused",
                portal=portal_name(name),
                reason=blocked.reason,
                until=format_time(until),
            )
        )

    async def _text(self, text: str) -> None:
        try:
            await self.notifier.send_text(text)
        except Exception as error:
            log.warning("could not send status message: %s", error)
