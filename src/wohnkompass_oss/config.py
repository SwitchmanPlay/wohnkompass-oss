"""Load and validate search.toml plus the secrets from the environment.

Everything is checked once at startup, so a typo fails immediately with the
name of the offending key instead of silently watching the wrong search.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DEALS, KINDS

CITIES = ("wien", "graz", "linz", "salzburg")
PORTALS = ("immowelt", "derstandard")
LANGUAGES = ("en", "de")
RED_FLAGS = ("befristet", "abloese", "provision", "wohnticket")
MIN_POLL_MINUTES = 15

# What a current desktop Chrome sends. Both portals refuse obviously automated
# User-Agents; politeness comes from the request rate, not from the header.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_POSTCODE_RE = re.compile(r"^[1-9]\d{3}$")


class ConfigError(ValueError):
    """The configuration is invalid; the message names the key to fix."""


@dataclass(frozen=True)
class Search:
    city: str
    deal: str = "rent"
    types: tuple[str, ...] = ("flat",)
    postcodes: tuple[str, ...] = ()
    price_min: float | None = None
    price_max: float | None = None
    size_min: float | None = None
    rooms_min: float | None = None
    exclude_red_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AISettings:
    enabled: bool = False
    min_score: int = 6
    photos: bool = False


@dataclass(frozen=True)
class Profile:
    name: str = ""
    about: str = ""
    move_in: str = ""


@dataclass(frozen=True)
class Settings:
    search: Search
    ai: AISettings
    profile: Profile
    language: str = "en"
    poll_minutes: int = 20
    portals: tuple[str, ...] = PORTALS
    db_path: Path = Path("wohnkompass.sqlite3")
    telegram_token: str | None = None
    chat_id: int | None = None
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_model: str | None = None
    proxy_url: str | None = None
    user_agent: str = DEFAULT_USER_AGENT

    def require_telegram(self) -> tuple[str, int]:
        if not self.telegram_token:
            raise ConfigError("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
        if self.chat_id is None:
            raise ConfigError("TELEGRAM_CHAT_ID is not set (see .env.example)")
        return self.telegram_token, self.chat_id


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
_TOP_KEYS = {"language", "poll_minutes", "portals", "database", "search", "ai", "profile"}
_SEARCH_KEYS = {
    "city",
    "deal",
    "types",
    "postcodes",
    "price_min",
    "price_max",
    "size_min",
    "rooms_min",
    "exclude_red_flags",
}
_AI_KEYS = {"enabled", "min_score", "photos"}
_PROFILE_KEYS = {"name", "about", "move_in"}


def load_settings(path: Path, env: Mapping[str, str]) -> Settings:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path} (copy search.example.toml)")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    _reject_unknown(raw, _TOP_KEYS, "")
    search = _load_search(_table(raw, "search"))
    ai = _load_ai(_table(raw, "ai"))
    profile = _load_profile(_table(raw, "profile"))

    language = raw.get("language", "en")
    if language not in LANGUAGES:
        raise ConfigError(f"language must be one of {LANGUAGES}, got {language!r}")

    poll = raw.get("poll_minutes", 20)
    if not isinstance(poll, int) or isinstance(poll, bool) or poll < MIN_POLL_MINUTES:
        raise ConfigError(f"poll_minutes must be a whole number >= {MIN_POLL_MINUTES}")

    portals = _str_list(raw.get("portals", list(PORTALS)), "portals", allowed=PORTALS)
    if not portals:
        raise ConfigError("portals must name at least one portal")

    database = raw.get("database", "wohnkompass.sqlite3")
    if not isinstance(database, str) or not database.strip():
        raise ConfigError("database must be a file path")
    db_path = Path(database)
    if not db_path.is_absolute():
        db_path = path.parent / db_path

    getenv = _env_reader(env)
    chat_raw = getenv("TELEGRAM_CHAT_ID")
    chat_id = None
    if chat_raw is not None:
        try:
            chat_id = int(chat_raw)
        except ValueError:
            raise ConfigError("TELEGRAM_CHAT_ID must be a number") from None

    settings = Settings(
        search=search,
        ai=ai,
        profile=profile,
        language=language,
        poll_minutes=poll,
        portals=portals,
        db_path=db_path,
        telegram_token=getenv("TELEGRAM_BOT_TOKEN"),
        chat_id=chat_id,
        ai_base_url=getenv("AI_BASE_URL"),
        ai_api_key=getenv("AI_API_KEY"),
        ai_model=getenv("AI_MODEL"),
        proxy_url=getenv("HTTP_PROXY_URL"),
        user_agent=getenv("USER_AGENT") or DEFAULT_USER_AGENT,
    )
    if ai.enabled:
        for key, value in (
            ("AI_BASE_URL", settings.ai_base_url),
            ("AI_API_KEY", settings.ai_api_key),
            ("AI_MODEL", settings.ai_model),
        ):
            if not value:
                raise ConfigError(f"[ai] enabled = true needs {key} in .env")
    return settings


def _load_search(data: dict[str, Any]) -> Search:
    _reject_unknown(data, _SEARCH_KEYS, "search.")
    city = data.get("city")
    if city not in CITIES:
        raise ConfigError(f"search.city must be one of {CITIES}, got {city!r}")
    deal = data.get("deal", "rent")
    if deal not in DEALS:
        raise ConfigError(f"search.deal must be one of {DEALS}, got {deal!r}")
    types = _str_list(data.get("types", ["flat"]), "search.types", allowed=KINDS)
    if not types:
        raise ConfigError("search.types must name at least one of " + str(KINDS))
    postcodes = _str_list(data.get("postcodes", []), "search.postcodes")
    for code in postcodes:
        if not _POSTCODE_RE.match(code):
            raise ConfigError(f"search.postcodes: {code!r} is not a 4-digit postcode")
    flags = _str_list(
        data.get("exclude_red_flags", []), "search.exclude_red_flags", allowed=RED_FLAGS
    )
    numbers = {
        key: _non_negative(data.get(key), f"search.{key}")
        for key in ("price_min", "price_max", "size_min", "rooms_min")
    }
    lo, hi = numbers["price_min"], numbers["price_max"]
    if lo is not None and hi is not None and lo > hi:
        raise ConfigError("search.price_min is larger than search.price_max")
    return Search(
        city=city,
        deal=deal,
        types=types,
        postcodes=postcodes,
        exclude_red_flags=flags,
        **numbers,
    )


def _load_ai(data: dict[str, Any]) -> AISettings:
    _reject_unknown(data, _AI_KEYS, "ai.")
    min_score = data.get("min_score", 6)
    if not isinstance(min_score, int) or isinstance(min_score, bool) or not 1 <= min_score <= 10:
        raise ConfigError("ai.min_score must be a whole number from 1 to 10")
    return AISettings(
        enabled=_bool(data.get("enabled", False), "ai.enabled"),
        min_score=min_score,
        photos=_bool(data.get("photos", False), "ai.photos"),
    )


def _load_profile(data: dict[str, Any]) -> Profile:
    _reject_unknown(data, _PROFILE_KEYS, "profile.")
    values = {}
    for key in _PROFILE_KEYS:
        value = data.get(key, "")
        if not isinstance(value, str):
            raise ConfigError(f"profile.{key} must be text")
        values[key] = value.strip()
    return Profile(**values)


# --------------------------------------------------------------------------
# Small validators
# --------------------------------------------------------------------------
def _table(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{key}] must be a table")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], prefix: str) -> None:
    for key in data:
        if key not in allowed:
            raise ConfigError(f"unknown setting {prefix}{key}")


def _str_list(value: Any, key: str, allowed: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} must be a list of strings")
    if allowed is not None:
        for item in value:
            if item not in allowed:
                raise ConfigError(f"{key}: {item!r} is not one of {allowed}")
    return tuple(dict.fromkeys(value))


def _non_negative(value: Any, key: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ConfigError(f"{key} must be a number >= 0")
    return float(value) if value else None


def _bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false")
    return value


def _env_reader(env: Mapping[str, str]):
    def get(key: str) -> str | None:
        value = env.get(key)
        if value is None:
            return None
        value = value.strip()
        return value or None

    return get
