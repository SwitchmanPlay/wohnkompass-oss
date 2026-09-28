"""The only way this project talks to a portal.

Politeness rules, all enforced here rather than trusted to callers:

* at least ``min_delay`` seconds (+ random jitter) between two requests to the
  same host;
* a 403, a 429, a captcha or a bot-check page raises :class:`Blocked`. The
  caller pauses that portal. Nothing here tries to get past a block: no
  retries with another identity, no captcha solving, no browser impersonation;
* timeouts and 5xx get exactly one retry, 30 seconds later.
"""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

PAUSE_BASE_SECONDS = 1800
PAUSE_MAX_SECONDS = 86_400
RETRY_DELAY_SECONDS = 30.0

# Only short pages are checked for bot-check markers: a real results page is
# 100+ KB and may legitimately mention reCAPTCHA in a script.
_CHALLENGE_MAX_BYTES = 30_000
_CAPTCHA_MARKERS = ("captcha",)
_BOT_CHECK_MARKERS = (
    "challenge-platform",
    "cf-browser-verification",
    "are you a robot",
    "bist du ein mensch",
    "access denied",
)


class Blocked(Exception):
    """The portal refused us. Pause it; do not retry."""

    def __init__(self, host: str, reason: str, retry_after: float | None = None):
        super().__init__(f"{host}: {reason}")
        self.host = host
        self.reason = reason
        self.retry_after = retry_after


class FetchError(Exception):
    """A network or server problem. Try again next cycle."""


def pause_seconds(strikes: int) -> int:
    """30 min for the first block, doubling each time, capped at 24 h."""
    return min(PAUSE_BASE_SECONDS * 2 ** max(strikes - 1, 0), PAUSE_MAX_SECONDS)


def looks_like_challenge(status: int, text: str) -> str | None:
    """Why this response is a block, or None when it is a normal page."""
    if status in (403, 429):
        return f"HTTP {status}"
    if status == 202 and not text.strip():
        return "empty HTTP 202 (bot check)"
    if len(text) < _CHALLENGE_MAX_BYTES:
        lowered = text.lower()
        if any(marker in lowered for marker in _CAPTCHA_MARKERS):
            return "captcha page"
        if any(marker in lowered for marker in _BOT_CHECK_MARKERS):
            return "bot check"
    return None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(when.timestamp() - time.time(), 0.0)


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        proxy: str | None = None,
        min_delay: float = 10.0,
        jitter: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        timeout: float = 30.0,
    ):
        self._min_delay = min_delay
        self._jitter = jitter
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._last_request: dict[str, float] = {}
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-AT,de;q=0.9,en;q=0.5",
            },
            proxy=proxy,
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url: str) -> str:
        host = urlsplit(url).hostname or url
        for attempt in (1, 2):
            await self._wait_for(host)
            try:
                response = await self._client.get(url)
            except httpx.TransportError as exc:
                error: Exception = exc
            else:
                reason = looks_like_challenge(response.status_code, response.text)
                if reason:
                    raise Blocked(host, reason, _retry_after(response))
                if response.status_code == 200:
                    return response.text
                error = FetchError(f"{host}: HTTP {response.status_code}")
                if response.status_code < 500:
                    raise error
            finally:
                self._last_request[host] = self._clock()
            if attempt == 1:
                await self._sleep(RETRY_DELAY_SECONDS)
        raise FetchError(str(error)) from error

    async def _wait_for(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is None:
            return
        delay = self._min_delay + self._rng() * self._jitter
        remaining = delay - (self._clock() - last)
        if remaining > 0:
            await self._sleep(remaining)
