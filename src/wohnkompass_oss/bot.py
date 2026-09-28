"""The Telegram side: alerts with buttons, a few commands, and the poll schedule.

Only the chat in ``TELEGRAM_CHAT_ID`` is served; every other chat is ignored.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from .ai import AIClient
from .config import Settings
from .i18n import PORTAL_NAMES, t
from .models import Listing
from .pipeline import CycleReport, Fetcher, Notifier, run_cycle
from .render import render_search, render_status
from .store import Store

log = logging.getLogger(__name__)

POLL_JITTER = 0.2
FIRST_CHECK_DELAY_SECONDS = 15


class TelegramNotifier:
    def __init__(self, bot, chat_id: int, language: str):
        self._bot = bot
        self._chat_id = chat_id
        self._language = language

    async def send_alert(self, listing: Listing, text: str, can_draft: bool) -> None:
        row = [InlineKeyboardButton(t(self._language, "button_open"), url=listing.url)]
        if can_draft:
            row.append(
                InlineKeyboardButton(
                    t(self._language, "button_draft"), callback_data=f"draft:{listing.id}"
                )
            )
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([row]),
        )

    async def send_text(self, text: str) -> None:
        await self._bot.send_message(chat_id=self._chat_id, text=text, parse_mode=ParseMode.HTML)


@dataclass
class BotRuntime:
    """Everything the handlers and the scheduled job share."""

    settings: Settings
    store: Store
    client: Fetcher | None
    ai: AIClient | None
    paused: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def check(self, notifier: Notifier) -> CycleReport | None:
        """Run one cycle, or return None when one is already running."""
        if self.lock.locked():
            return None
        async with self.lock:
            return await run_cycle(self.settings, self.store, self.client, notifier, ai=self.ai)


async def draft_letter(runtime: BotRuntime, listing_id: str) -> str:
    lang = runtime.settings.language
    listing = runtime.store.get(listing_id)
    if listing is None:
        return t(lang, "letter_gone")
    letter = None
    if runtime.ai is not None:
        letter = await runtime.ai.letter(listing, runtime.settings.profile, lang)
    return letter or t(lang, "letter_failed")


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def _runtime(context: ContextTypes.DEFAULT_TYPE) -> BotRuntime:
    return context.application.bot_data["runtime"]


def _notifier(context: ContextTypes.DEFAULT_TYPE) -> TelegramNotifier:
    runtime = _runtime(context)
    return TelegramNotifier(context.bot, runtime.settings.chat_id, runtime.settings.language)


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _runtime(context).settings
    portals = ", ".join(PORTAL_NAMES[name] for name in settings.portals)
    await update.effective_message.reply_text(
        t(
            settings.language,
            "start",
            portals=portals,
            search=render_search(settings.search, settings.language),
            minutes=settings.poll_minutes,
        )
    )


async def _status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _runtime(context)
    states = [(name, runtime.store.portal_state(name)) for name in runtime.settings.portals]
    text = render_status(runtime.settings.language, states, runtime.store.count(), runtime.paused)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def _pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _runtime(context)
    runtime.paused = True
    await update.effective_message.reply_text(t(runtime.settings.language, "paused"))


async def _resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _runtime(context)
    runtime.paused = False
    await update.effective_message.reply_text(t(runtime.settings.language, "resumed"))


async def _check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _runtime(context)
    lang = runtime.settings.language
    await update.effective_message.reply_text(t(lang, "check_started"))
    report = await runtime.check(_notifier(context))
    if report is None:
        await update.effective_message.reply_text(t(lang, "check_busy"))
        return
    await update.effective_message.reply_text(
        t(lang, "check_done", fetched=report.fetched, alerts=report.alerts)
    )


async def _draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _runtime(context)
    query = update.callback_query
    if update.effective_chat is None or update.effective_chat.id != runtime.settings.chat_id:
        return
    await query.answer(t(runtime.settings.language, "letter_working"))
    listing_id = (query.data or "").removeprefix("draft:")
    letter = await draft_letter(runtime, listing_id)
    await query.message.reply_text(letter)


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------
def _next_delay(settings: Settings) -> float:
    base = settings.poll_minutes * 60
    return base * (1 + random.uniform(-POLL_JITTER, POLL_JITTER))


async def _poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _runtime(context)
    try:
        if not runtime.paused:
            await runtime.check(_notifier(context))
    except Exception:
        log.exception("poll cycle failed")
    finally:
        delay = _next_delay(runtime.settings)
        log.info("next check in %.1f min", delay / 60)
        context.job_queue.run_once(_poll_job, delay, name="poll")


def build_application(settings: Settings, runtime: BotRuntime) -> Application:
    token, chat_id = settings.require_telegram()
    app = ApplicationBuilder().token(token).build()
    app.bot_data["runtime"] = runtime
    owner = filters.Chat(chat_id=chat_id)
    for name, callback in (
        ("start", _start),
        ("status", _status),
        ("pause", _pause),
        ("resume", _resume),
        ("check", _check),
    ):
        app.add_handler(CommandHandler(name, callback, filters=owner))
    app.add_handler(CallbackQueryHandler(_draft, pattern=r"^draft:"))
    if app.job_queue is not None:
        app.job_queue.run_once(_poll_job, FIRST_CHECK_DELAY_SECONDS, name="poll")
    return app
