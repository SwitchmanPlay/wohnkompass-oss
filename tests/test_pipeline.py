from dataclasses import replace

import pytest

from conftest import make_listing
from wohnkompass_oss.ai import Score
from wohnkompass_oss.config import AISettings, Profile, Search, Settings
from wohnkompass_oss.http import Blocked, FetchError
from wohnkompass_oss.pipeline import EMPTY_ALERT_AFTER, MAX_SEND_ATTEMPTS, run_cycle
from wohnkompass_oss.store import Store

SETTINGS = Settings(
    search=Search(city="wien", price_max=1200),
    ai=AISettings(),
    profile=Profile(),
    portals=("immowelt", "derstandard"),
)


class FakeAdapter:
    def __init__(self, name: str):
        self.name = name
        self.host = f"{name}.test"
        self.listings: list = []

    def build_url(self, search, kind):
        return f"https://{self.host}/{search.deal}/{kind}"

    def parse(self, html, deal, kind):
        return list(self.listings)


class FakeClient:
    def __init__(self):
        self.errors: dict[str, Exception] = {}
        self.urls: list[str] = []

    async def get(self, url):
        self.urls.append(url)
        for host, error in self.errors.items():
            if host in url:
                raise error
        return "<html></html>"


class FakeNotifier:
    def __init__(self):
        self.alerts: list[tuple[str, str, bool]] = []
        self.texts: list[str] = []
        self.fail = False

    async def send_alert(self, listing, text, can_draft):
        if self.fail:
            raise RuntimeError("telegram down")
        self.alerts.append((listing.id, text, can_draft))

    async def send_text(self, text):
        self.texts.append(text)


class FakeAI:
    def __init__(self, score=None):
        self.result = score
        self.calls = 0

    async def score(self, listing, search, language, photos):
        self.calls += 1
        return self.result


def immowelt(**kw):
    return make_listing(source="immowelt", **kw)


def derstandard(**kw):
    return make_listing(
        source="derstandard", url="https://immobilien.derstandard.at/detail/1", **kw
    )


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    adapters = {"immowelt": FakeAdapter("immowelt"), "derstandard": FakeAdapter("derstandard")}
    client, notifier = FakeClient(), FakeNotifier()

    async def cycle(now=100_000.0, settings=SETTINGS, ai=None):
        return await run_cycle(settings, store, client, notifier, adapters=adapters, ai=ai, now=now)

    yield store, adapters, client, notifier, cycle
    store.close()


async def test_first_run_only_fills_the_pool(env):
    store, adapters, _, notifier, cycle = env
    adapters["immowelt"].listings = [immowelt(source_id="1"), immowelt(source_id="2")]
    report = await cycle()
    assert notifier.alerts == []
    assert store.count() == 2
    assert report.fetched == 2


async def test_second_run_alerts_new_matching_listings_once(env):
    _, adapters, _, notifier, cycle = env
    adapters["immowelt"].listings = [immowelt(source_id="1")]
    await cycle()
    adapters["immowelt"].listings = [
        immowelt(source_id="1"),
        immowelt(source_id="2", price=900.0),
        immowelt(source_id="3", price=5000.0),  # over budget
    ]
    report = await cycle()
    assert [alert[0] for alert in notifier.alerts] == ["immowelt:2"]
    assert report.alerts == 1
    await cycle()
    assert len(notifier.alerts) == 1


async def test_one_request_per_kind_and_portal(env):
    _, _, client, _, cycle = env
    settings = replace(SETTINGS, search=replace(SETTINGS.search, types=("flat", "house")))
    await cycle(settings=settings)
    assert sorted(client.urls) == [
        "https://derstandard.test/rent/flat",
        "https://derstandard.test/rent/house",
        "https://immowelt.test/rent/flat",
        "https://immowelt.test/rent/house",
    ]


async def test_implausible_listings_are_dropped(env):
    store, adapters, _, notifier, cycle = env
    adapters["immowelt"].listings = [immowelt(source_id="1")]
    await cycle()
    adapters["immowelt"].listings = [immowelt(source_id="2", postcode="70173")]
    await cycle()
    assert notifier.alerts == []
    assert store.get("immowelt:2") is None


async def test_cross_portal_duplicate_is_sent_once(env):
    store, adapters, _, notifier, cycle = env
    adapters["immowelt"].listings = [immowelt(source_id="0")]
    adapters["derstandard"].listings = [derstandard(source_id="0")]
    await cycle()
    flat = {"price": 1000.0, "size_m2": 60.0, "postcode": "1070"}
    adapters["immowelt"].listings = [immowelt(source_id="1", title="Wohnung A", **flat)]
    adapters["derstandard"].listings = [derstandard(source_id="7", title="Wohnung B", **flat)]
    await cycle()
    assert [alert[0] for alert in notifier.alerts] == ["immowelt:1"]
    assert store.also_on("immowelt:1") == ["derstandard"]


async def test_price_drop_into_budget_alerts(env):
    _, adapters, _, notifier, cycle = env
    adapters["immowelt"].listings = [immowelt(source_id="0")]
    await cycle()
    adapters["immowelt"].listings = [immowelt(source_id="1", price=1500.0)]
    await cycle()
    assert notifier.alerts == []
    adapters["immowelt"].listings = [immowelt(source_id="1", price=1100.0)]
    await cycle()
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0][1].startswith("📉")


async def test_ai_score_below_minimum_is_not_sent(env):
    _, adapters, _, notifier, cycle = env
    settings = replace(SETTINGS, ai=AISettings(enabled=True, min_score=6))
    adapters["immowelt"].listings = [immowelt(source_id="0")]
    await cycle(settings=settings)
    adapters["immowelt"].listings = [immowelt(source_id="1")]
    ai = FakeAI(Score(4, "Too expensive."))
    await cycle(settings=settings, ai=ai)
    assert notifier.alerts == []
    assert ai.calls == 1


async def test_ai_failure_still_sends_the_alert(env):
    _, adapters, _, notifier, cycle = env
    settings = replace(SETTINGS, ai=AISettings(enabled=True))
    adapters["immowelt"].listings = [immowelt(source_id="0")]
    await cycle(settings=settings)
    adapters["immowelt"].listings = [immowelt(source_id="1")]
    await cycle(settings=settings, ai=FakeAI(None))
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0][2] is True  # letters can be drafted


async def test_good_ai_score_is_shown(env):
    _, adapters, _, notifier, cycle = env
    settings = replace(SETTINGS, ai=AISettings(enabled=True))
    adapters["immowelt"].listings = [immowelt(source_id="0")]
    await cycle(settings=settings)
    adapters["immowelt"].listings = [immowelt(source_id="1")]
    await cycle(settings=settings, ai=FakeAI(Score(9, "Great deal.")))
    assert "9/10" in notifier.alerts[0][1]


async def test_block_pauses_the_portal_and_the_other_one_still_runs(env):
    store, adapters, client, notifier, cycle = env
    client.errors["immowelt"] = Blocked("immowelt.test", "HTTP 429", retry_after=None)
    adapters["derstandard"].listings = [derstandard(source_id="1")]
    report = await cycle(now=1_000.0)
    state = store.portal_state("immowelt")
    assert state.paused_until == 1_000.0 + 1800
    assert state.strikes == 1
    assert report.blocked == ["immowelt"]
    assert store.has_rows("derstandard")
    assert len(notifier.texts) == 1
    assert "HTTP 429" in notifier.texts[0]

    # While paused, the portal is not contacted at all.
    client.urls.clear()
    report = await cycle(now=2_000.0)
    assert report.skipped == ["immowelt"]
    assert not any("immowelt" in url for url in client.urls)


async def test_repeated_blocks_escalate_and_honour_retry_after(env):
    store, _, client, _, cycle = env
    client.errors["immowelt"] = Blocked("immowelt.test", "HTTP 403")
    await cycle(now=0.0)
    await cycle(now=10_000.0)
    assert store.portal_state("immowelt").paused_until == 10_000.0 + 3600
    client.errors["immowelt"] = Blocked("immowelt.test", "HTTP 429", retry_after=50_000.0)
    await cycle(now=20_000.0)
    assert store.portal_state("immowelt").paused_until == 20_000.0 + 50_000.0


async def test_success_after_a_pause_clears_it(env):
    store, adapters, client, _, cycle = env
    client.errors["immowelt"] = Blocked("immowelt.test", "HTTP 429")
    await cycle(now=0.0)
    del client.errors["immowelt"]
    adapters["immowelt"].listings = [immowelt(source_id="1")]
    await cycle(now=5_000.0)
    assert store.portal_state("immowelt").strikes == 0


async def test_fetch_error_is_reported_not_raised(env):
    _, _, client, notifier, cycle = env
    client.errors["derstandard"] = FetchError("timeout")
    report = await cycle()
    assert report.errors == ["derstandard: timeout"]
    assert notifier.texts == []


async def test_repeated_empty_pages_warn_once(env):
    _, _, _, notifier, cycle = env
    for index in range(EMPTY_ALERT_AFTER + 2):
        await cycle(now=float(index))
    warnings = [text for text in notifier.texts if "no listings" in text]
    assert len(warnings) == 2  # one per portal, not one per cycle


async def test_failed_send_is_retried_then_given_up(env):
    store, adapters, _, notifier, cycle = env
    adapters["immowelt"].listings = [immowelt(source_id="0")]
    await cycle()
    adapters["immowelt"].listings = [immowelt(source_id="1")]
    notifier.fail = True
    await cycle()
    assert not store.was_notified("immowelt:1")

    notifier.fail = False
    await cycle()
    assert [alert[0] for alert in notifier.alerts] == ["immowelt:1"]
    assert store.was_notified("immowelt:1")


async def test_send_gives_up_after_max_attempts(env):
    _, adapters, _, notifier, cycle = env
    adapters["immowelt"].listings = [immowelt(source_id="0")]
    await cycle()
    adapters["immowelt"].listings = [immowelt(source_id="1")]
    notifier.fail = True
    for _ in range(MAX_SEND_ATTEMPTS + 2):
        await cycle()
    notifier.fail = False
    await cycle()
    assert notifier.alerts == []


async def test_old_listings_are_pruned(env):
    store, adapters, _, _, cycle = env
    adapters["immowelt"].listings = [immowelt(source_id="old")]
    await cycle(now=0.0)
    adapters["immowelt"].listings = [immowelt(source_id="new")]
    await cycle(now=40 * 86_400.0)
    assert store.get("immowelt:old") is None
