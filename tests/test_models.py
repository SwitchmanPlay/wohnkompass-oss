from conftest import make_listing


def test_id_combines_source_and_source_id():
    assert make_listing(source="derstandard", source_id="42").id == "derstandard:42"


def test_warm_price_is_used_as_is():
    listing = make_listing(price=900.0, price_is_net=False)
    assert listing.effective_price == 900.0
    assert listing.price_is_estimate is False


def test_net_rent_gets_estimated_running_costs():
    listing = make_listing(price=700.0, price_is_net=True, size_m2=50.0)
    assert listing.effective_price == 700.0 + 3.6 * 50.0
    assert listing.price_is_estimate is True


def test_net_rent_without_size_stays_an_estimate():
    listing = make_listing(price=700.0, price_is_net=True, size_m2=None)
    assert listing.effective_price == 700.0
    assert listing.price_is_estimate is True


def test_purchase_price_is_never_adjusted():
    listing = make_listing(deal="buy", price=300_000.0, price_is_net=True, size_m2=60.0)
    assert listing.effective_price == 300_000.0
    assert listing.price_is_estimate is False


def test_price_per_m2():
    assert make_listing(price=1000.0, size_m2=50.0).price_per_m2 == 20.0
    assert make_listing(price=None).price_per_m2 is None
    assert make_listing(size_m2=None).price_per_m2 is None
