"""Command line: ``wohnkompass-oss run | once [--dry-run] | check-config``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .ai import AIClient
from .config import ConfigError, Settings, load_settings
from .http import PoliteClient
from .i18n import PORTAL_NAMES
from .pipeline import ConsoleNotifier, run_cycle
from .render import render_search
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wohnkompass-oss",
        description="Telegram alerts for new flats on immowelt.at and derStandard.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", help="path to search.toml (default: $SEARCH_CONFIG)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="start the bot and check on a schedule")
    once = commands.add_parser("once", help="run a single check and exit")
    once.add_argument(
        "--dry-run",
        action="store_true",
        help="print current matches to the console; no Telegram, no database changes",
    )
    commands.add_parser("check-config", help="validate search.toml and .env")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    load_dotenv()
    config_path = Path(args.config or os.environ.get("SEARCH_CONFIG") or "search.toml")
    try:
        settings = load_settings(config_path, os.environ)
        if args.command == "check-config":
            _print_summary(settings)
            return 0
        if args.command == "once":
            return asyncio.run(_once(settings, dry_run=args.dry_run))
        return _run(settings)
    except ConfigError as error:
        print(f"config error: {error}", file=sys.stderr)
        return 2


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def _print_summary(settings: Settings) -> None:
    portals = ", ".join(PORTAL_NAMES[name] for name in settings.portals)
    telegram = "configured" if settings.telegram_token and settings.chat_id else "not configured"
    ai = f"on ({settings.ai_model})" if settings.ai.enabled else "off"
    print(f"Search:   {render_search(settings.search, 'en')}")
    print(f"Portals:  {portals}, every ~{settings.poll_minutes} min")
    print(f"Database: {settings.db_path}")
    print(f"Telegram: {telegram}")
    print(f"AI:       {ai}")
    print(f"Proxy:    {'yes' if settings.proxy_url else 'no'}")


def _clients(settings: Settings) -> tuple[PoliteClient, AIClient | None]:
    client = PoliteClient(settings.user_agent, proxy=settings.proxy_url)
    ai = None
    if settings.ai.enabled:
        ai = AIClient(settings.ai_base_url, settings.ai_api_key, settings.ai_model)
    return client, ai


async def _once(settings: Settings, dry_run: bool) -> int:
    client, ai = _clients(settings)
    try:
        if dry_run:
            report = await _dry_run(settings, client, ai)
        else:
            from telegram import Bot

            from .bot import TelegramNotifier

            token, chat_id = settings.require_telegram()
            store = Store(settings.db_path)
            try:
                async with Bot(token) as bot:
                    notifier = TelegramNotifier(bot, chat_id, settings.language)
                    report = await run_cycle(settings, store, client, notifier, ai=ai)
            finally:
                store.close()
    finally:
        await client.aclose()
        if ai is not None:
            await ai.aclose()
    print(
        f"checked {report.fetched} listings, {report.alerts} matches"
        + (f", blocked: {', '.join(report.blocked)}" if report.blocked else "")
        + (f", errors: {'; '.join(report.errors)}" if report.errors else ""),
        file=sys.stderr,
    )
    return 0


async def _dry_run(settings: Settings, client: PoliteClient, ai: AIClient | None):
    """Print current matches using a throwaway pool.

    Portal pauses are shared with the real database in both directions, so a
    dry run never hits a portal that blocked us, and a new block is remembered.
    """
    real = Store(settings.db_path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Store(Path(tmp) / "dry-run.sqlite3")
            try:
                for name in settings.portals:
                    state = real.portal_state(name)
                    if state.paused_until:
                        scratch.set_pause(name, state.paused_until, state.strikes, state.reason)
                report = await run_cycle(
                    settings, scratch, client, ConsoleNotifier(), ai=ai, alert_on_first_run=True
                )
                for name in report.blocked:
                    state = scratch.portal_state(name)
                    real.set_pause(name, state.paused_until, state.strikes, state.reason)
            finally:
                scratch.close()
    finally:
        real.close()
    return report


def _run(settings: Settings) -> int:
    from .bot import BotRuntime, build_application

    settings.require_telegram()
    client, ai = _clients(settings)
    store = Store(settings.db_path)
    runtime = BotRuntime(settings, store, client, ai)
    app = build_application(settings, runtime)

    async def shutdown(_app) -> None:
        await client.aclose()
        if ai is not None:
            await ai.aclose()
        store.close()

    app.post_shutdown = shutdown
    logging.getLogger(__name__).info(
        "watching %s: %s", ", ".join(settings.portals), render_search(settings.search, "en")
    )
    app.run_polling(allowed_updates=["message", "callback_query"])
    return 0
