import pytest

from conftest import make_listing
from wohnkompass_oss.config import Search
from wohnkompass_oss.dedup import is_duplicate, normalize_title
from wohnkompass_oss.filters import matches, red_flags
from wohnkompass_oss.plausibility import implausible

SEARCH = Search(
    city="wien",
    deal="rent",
    types=("flat",),
    price_min=500,
    price_max=1200,
    size_min=45,
    rooms_min=2,
)


# --------------------------------------------------------------------------
# Plausibility
# --------------------------------------------------------------------------
def test_normal_listing_is_plausible():
    assert implausible(make_listing()) is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"postcode": "70173"}, "postcode"),
        ({"postcode": "0999"}, "postcode"),
        ({"price": 20.0}, "rent"),
        ({"price": 40_000.0}, "rent"),
        ({"deal": "buy", "price": 5_000.0}, "purchase price"),
        ({"size_m2": 2.0}, "size"),
        ({"price": 5000.0, "size_m2": 20.0}, "€/m²"),
        ({"price": 60.0, "size_m2": 50.0}, "€/m²"),
    ],
)
def test_implausible_listings(overrides, reason):
    assert reason in implausible(make_listing(**overrides))


def test_missing_values_are_not_implausible():
    assert implausible(make_listing(price=None, size_m2=None, postcode=None)) is None


def test_buy_prices_skip_the_rent_per_m2_check():
    assert implausible(make_listing(deal="buy", price=450_000.0, size_m2=70.0)) is None


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------
def test_matching_listing():
    assert matches(make_listing(price=950.0, size_m2=55.0, rooms=2.0), SEARCH)


@pytest.mark.parametrize(
    "overrides",
    [
        {"deal": "buy"},
        {"kind": "house"},
        {"price": 1300.0},
        {"price": 400.0},
        {"size_m2": 40.0},
        {"rooms": 1.0},
    ],
)
def test_listing_outside_the_search(overrides):
    assert not matches(make_listing(**overrides), SEARCH)


def test_estimated_warm_rent_is_compared_with_the_budget():
    # 1000 net + 3.6 * 60 m² = 1216 warm, just over the 1200 budget.
    assert not matches(make_listing(price=1000.0, price_is_net=True, size_m2=60.0), SEARCH)
    assert matches(make_listing(price=900.0, price_is_net=True, size_m2=60.0), SEARCH)


def test_missing_price_fails_when_a_budget_is_set():
    assert not matches(make_listing(price=None), SEARCH)
    assert matches(make_listing(price=None), Search(city="wien"))


def test_missing_size_and_rooms_pass():
    assert matches(make_listing(size_m2=None, rooms=None), SEARCH)


def test_postcode_filter():
    search = Search(city="wien", postcodes=("1070", "1080"))
    assert matches(make_listing(postcode="1070"), search)
    assert not matches(make_listing(postcode="1100"), search)
    assert not matches(make_listing(postcode=None), search)


def test_excluded_red_flag_drops_the_listing():
    search = Search(city="wien", exclude_red_flags=("befristet",))
    assert not matches(make_listing(description="Befristet auf 3 Jahre."), search)
    assert matches(make_listing(description="Unbefristet, ab sofort."), search)


# --------------------------------------------------------------------------
# Red flags
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "flags"),
    [
        ("Befristet auf 3 Jahre", ["befristet"]),
        ("Mietvertrag mit Befristung 5 Jahre", ["befristet"]),
        ("fixed-term lease for 2 years", ["befristet"]),
        ("unbefristeter Mietvertrag", []),
        ("Ablöse für die Küche 4.000 €", ["abloese"]),
        ("keine Ablöse, ablösefrei", []),
        ("Provision: 2 Bruttomonatsmieten", ["provision"]),
        ("provisionsfrei direkt vom Eigentümer", []),
        ("Gemeindewohnung, nur mit Wohnticket", ["wohnticket"]),
        ("Befristet 3 Jahre, Ablöse 2.000 €", ["befristet", "abloese"]),
        ("", []),
    ],
)
def test_red_flags(text, flags):
    assert red_flags(make_listing(title="Wohnung", description=text)) == flags


def test_red_flags_read_the_title_too():
    assert red_flags(make_listing(title="Befristete Wohnung", description="")) == ["befristet"]


def test_befristung_is_irrelevant_when_buying():
    assert red_flags(make_listing(deal="buy", description="befristet vermietet")) == []


# --------------------------------------------------------------------------
# Dedup
# --------------------------------------------------------------------------
def test_normalize_title_ignores_case_order_and_umlauts():
    assert normalize_title("Schöne Wohnung, 3 Zimmer!") == normalize_title(
        "3 zimmer schoene wohnung"
    )


def test_same_flat_on_two_portals():
    a = make_listing(source="immowelt", source_id="1", price=1000.0, size_m2=60.0, postcode="1070")
    b = make_listing(
        source="derstandard", source_id="9", price=1020.0, size_m2=61.5, postcode="1070"
    )
    assert is_duplicate(a, b)


@pytest.mark.parametrize(
    "overrides",
    [{"postcode": "1080"}, {"price": 1100.0}, {"size_m2": 65.0}, {"postcode": None}],
)
def test_different_flats(overrides):
    a = make_listing(source="immowelt", title="A", price=1000.0, size_m2=60.0)
    b = make_listing(
        source="derstandard", title="B", **{"price": 1000.0, "size_m2": 60.0, **overrides}
    )
    assert not is_duplicate(a, b)


def test_near_identical_titles_are_duplicates():
    a = make_listing(
        source="immowelt", title="Sonnige 3-Zimmer-Wohnung mit Balkon in 1070", price=None
    )
    b = make_listing(source="derstandard", title="3-Zimmer-Wohnung, sonnige, mit Balkon in 1070!")
    assert is_duplicate(a, b)


def test_short_generic_titles_never_dedup():
    a = make_listing(source="immowelt", title="Wohnung", price=500.0, postcode="1010")
    b = make_listing(source="derstandard", title="Wohnung", price=900.0, postcode="1100")
    assert not is_duplicate(a, b)


def test_same_portal_is_never_a_cross_portal_duplicate():
    a = make_listing(source_id="1")
    b = make_listing(source_id="2")
    assert not is_duplicate(a, b)
