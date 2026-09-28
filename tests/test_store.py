import pytest

from conftest import make_listing
from wohnkompass_oss.store import Store

DAY = 86_400.0


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "test.sqlite3")
    yield db
    db.close()


def test_new_then_seen(store):
    listing = make_listing()
    assert store.upsert(listing, now=1.0) == "new"
    assert store.upsert(listing, now=2.0) == "seen"
    assert store.count() == 1


def test_price_drop_and_rise(store):
    store.upsert(make_listing(price=1000.0), now=1.0)
    assert store.upsert(make_listing(price=900.0), now=2.0) == "price_drop"
    assert store.previous_price(make_listing().id) == 1000.0
    assert store.upsert(make_listing(price=950.0), now=3.0) == "seen"
    # A tiny change (rounding on the portal) is not a price drop.
    assert store.upsert(make_listing(price=949.5), now=4.0) == "seen"


def test_listing_round_trip(store):
    listing = make_listing(photos=("https://example.com/a.jpg",), price_is_net=True)
    store.upsert(listing, now=1.0)
    assert store.get(listing.id) == listing
    assert store.get("nope:1") is None


def test_has_rows_per_source(store):
    assert not store.has_rows("immowelt")
    store.upsert(make_listing(source="immowelt"), now=1.0)
    assert store.has_rows("immowelt")
    assert not store.has_rows("derstandard")


def test_notified_bookkeeping(store):
    listing = make_listing()
    store.upsert(listing, now=1.0)
    assert not store.was_notified(listing.id)
    assert store.record_send_failure(listing.id) == 1
    assert store.record_send_failure(listing.id) == 2
    store.mark_notified(listing.id, now=5.0)
    assert store.was_notified(listing.id)


def test_pending_retries(store):
    listing = make_listing()
    store.upsert(listing, now=1.0)
    store.record_send_failure(listing.id)
    assert [item.id for item in store.pending_retries(max_failures=3)] == [listing.id]
    store.record_send_failure(listing.id)
    store.record_send_failure(listing.id)
    assert store.pending_retries(max_failures=3) == []


def test_notified_listings_for_dedup(store):
    mine = make_listing(source="immowelt", source_id="1")
    other = make_listing(source="derstandard", source_id="2")
    for item in (mine, other):
        store.upsert(item, now=1.0)
        store.mark_notified(item.id, now=1.0)
    store.upsert(make_listing(source="derstandard", source_id="3"), now=1.0)
    found = store.notified_listings(exclude_source="immowelt")
    assert [item.id for item in found] == [other.id]


def test_also_on(store):
    listing = make_listing()
    store.upsert(listing, now=1.0)
    store.add_also_on(listing.id, "derstandard")
    store.add_also_on(listing.id, "derstandard")
    assert store.also_on(listing.id) == ["derstandard"]


def test_prune_removes_old_rows(store):
    store.upsert(make_listing(source_id="old"), now=0.0)
    store.upsert(make_listing(source_id="new"), now=29 * DAY)
    assert store.prune(now=31 * DAY, days=30) == 1
    assert store.count() == 1


def test_portal_state_survives_reopen(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = Store(path)
    first.set_pause("immowelt", until=5000.0, strikes=2, reason="HTTP 429")
    first.set_last_run("immowelt", now=100.0, count=30)
    first.close()

    second = Store(path)
    state = second.portal_state("immowelt")
    assert state.paused_until == 5000.0
    assert state.strikes == 2
    assert state.reason == "HTTP 429"
    assert state.last_run == 100.0
    assert state.last_count == 30
    second.clear_pause("immowelt")
    assert second.portal_state("immowelt").paused_until is None
    assert second.portal_state("immowelt").strikes == 0
    second.close()


def test_empty_streak(store):
    assert store.record_empty("derstandard") == 1
    assert store.record_empty("derstandard") == 2
    store.reset_empty("derstandard")
    assert store.portal_state("derstandard").empty_streak == 0


def test_unknown_portal_state_is_blank(store):
    state = store.portal_state("immowelt")
    assert state.paused_until is None
    assert state.strikes == 0
    assert state.last_run is None
