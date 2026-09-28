from dataclasses import replace

import pytest
from telegram.ext import CallbackQueryHandler, CommandHandler

from conftest import make_listing
from wohnkompass_oss.bot import BotRuntime, TelegramNotifier, build_application, draft_letter
from wohnkompass_oss.config import AISettings, Profile, Search, Settings
from wohnkompass_oss.store import Store

SETTINGS = Settings(
    search=Search(city="wien"),
    ai=AISettings(),
    profile=Profile(about="Nurse"),
    telegram_token="123456:TEST-TOKEN",
    chat_id=4242,
)


class FakeAI:
    def __init__(self, letter):
        self.result = letter

    async def letter(self, listing, profile, language):
        return self.result


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "bot.sqlite3")
    yield db
    db.close()


def test_application_only_listens_to_the_owner(store):
    runtime = BotRuntime(SETTINGS, store, client=None, ai=None)
    app = build_application(SETTINGS, runtime)
    handlers = [h for group in app.handlers.values() for h in group]
    commands = {c for h in handlers if isinstance(h, CommandHandler) for c in h.commands}
    assert commands == {"start", "status", "pause", "resume", "check"}
    for handler in handlers:
        if isinstance(handler, CommandHandler):
            assert 4242 in handler.filters.chat_ids
    assert any(isinstance(h, CallbackQueryHandler) for h in handlers)


async def test_notifier_adds_buttons():
    bot = FakeBot()
    notifier = TelegramNotifier(bot, chat_id=4242, language="en")
    await notifier.send_alert(make_listing(), "<b>hi</b>", can_draft=True)
    message = bot.sent[0]
    assert message["chat_id"] == 4242
    assert message["parse_mode"] == "HTML"
    buttons = message["reply_markup"].inline_keyboard[0]
    assert buttons[0].url == "https://www.immowelt.at/expose/abc-1"
    assert buttons[1].callback_data == "draft:immowelt:abc-1"


async def test_notifier_without_ai_has_no_draft_button():
    bot = FakeBot()
    await TelegramNotifier(bot, 1, "de").send_alert(make_listing(), "x", can_draft=False)
    assert len(bot.sent[0]["reply_markup"].inline_keyboard[0]) == 1


async def test_draft_letter(store):
    listing = make_listing()
    store.upsert(listing, now=1.0)
    runtime = BotRuntime(SETTINGS, store, client=None, ai=FakeAI("Sehr geehrte ..."))
    assert await draft_letter(runtime, listing.id) == "Sehr geehrte ..."


async def test_draft_letter_failures(store):
    runtime = BotRuntime(SETTINGS, store, client=None, ai=FakeAI(None))
    assert (
        await draft_letter(runtime, "immowelt:missing")
        == "This listing is no longer in the database."
    )
    store.upsert(make_listing(), now=1.0)
    assert "could not be written" in await draft_letter(runtime, "immowelt:abc-1")


async def test_runtime_refuses_overlapping_checks(store):
    runtime = BotRuntime(replace(SETTINGS, portals=()), store, client=None, ai=None)
    notifier = TelegramNotifier(FakeBot(), 1, "en")
    async with runtime.lock:
        assert await runtime.check(notifier) is None
    report = await runtime.check(notifier)
    assert report is not None
