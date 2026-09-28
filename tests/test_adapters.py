import pytest

from conftest import fixture_html
from wohnkompass_oss.adapters import ADAPTERS, get_adapter
from wohnkompass_oss.config import Search

VIENNA_RENT = Search(city="wien")


def test_registry_knows_both_portals():
    assert set(ADAPTERS) == {"immowelt", "derstandard"}
    assert get_adapter("immowelt").host == "www.immowelt.at"
    assert get_adapter("derstandard").host == "immobilien.derstandard.at"
    with pytest.raises(KeyError):
        get_adapter("willhaben")


# --------------------------------------------------------------------------
# immowelt
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("search", "kind", "expected"),
    [
        (
            VIENNA_RENT,
            "flat",
            "https://www.immowelt.at/classified-search?distributionTypes=Rent"
            "&estateTypes=Apartment&locations=AD08AT2093&order=DateDesc",
        ),
        (
            Search(city="graz", deal="buy"),
            "house",
            "https://www.immowelt.at/classified-search?distributionTypes=Buy"
            "&estateTypes=House&locations=AD06AT68&order=DateDesc",
        ),
    ],
)
def test_immowelt_url(search, kind, expected):
    assert get_adapter("immowelt").build_url(search, kind) == expected


def test_immowelt_parses_cards():
    listings = get_adapter("immowelt").parse(fixture_html("immowelt_search.html"), "rent", "flat")
    assert [item.source_id[-3:] for item in listings] == ["001", "002", "003"]

    first = listings[0]
    assert first.url == "https://www.immowelt.at/expose/0a1b2c3d-0000-4000-8000-000000000001"
    assert first.price == 890.5
    assert first.price_is_net is True
    assert first.rooms == 2.0
    assert first.size_m2 == 54.0
    assert first.postcode == "1070"
    assert first.address == "Neubau, Wien"
    assert first.title == "Helle Altbauwohnung nahe der U3."
    assert "Befristet auf 3 Jahre" in first.description
    assert first.photos == (
        "https://mms.immowelt.de/0/0/0/a/0000000a-0000-4000-8000-00000000000a.jpg?w=525",
        "https://mms.immowelt.de/0/0/0/b/0000000b-0000-4000-8000-00000000000b.jpg?w=525",
    )


def test_immowelt_card_without_size_or_real_text():
    second = get_adapter("immowelt").parse(fixture_html("immowelt_search.html"), "rent", "flat")[1]
    assert second.size_m2 is None
    assert second.rooms == 3.0
    assert second.price == 1240.0
    assert second.postcode == "1120"
    # "Ok" is too short to be a title; fall back to the covering link's title.
    assert second.title == "Wohnung zur Miete - Wien - 1.240 € - 3 Zimmer"


def test_immowelt_card_without_price():
    third = get_adapter("immowelt").parse(fixture_html("immowelt_search.html"), "rent", "flat")[2]
    assert third.price is None
    assert third.size_m2 == 48.5
    assert third.postcode is None


# --------------------------------------------------------------------------
# derStandard
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("search", "kind", "expected"),
    [
        (VIENNA_RENT, "flat", "https://immobilien.derstandard.at/suche/wien/mieten-wohnung"),
        (
            Search(city="salzburg", deal="buy"),
            "house",
            "https://immobilien.derstandard.at/suche/salzburg-stadt/kaufen-haus",
        ),
    ],
)
def test_derstandard_url(search, kind, expected):
    assert get_adapter("derstandard").build_url(search, kind) == expected


def test_derstandard_parses_cards_and_skips_projects():
    listings = get_adapter("derstandard").parse(
        fixture_html("derstandard_search.html"), "rent", "flat"
    )
    assert [item.source_id for item in listings] == ["90000001", "90000002"]

    first = listings[0]
    assert first.url == "https://immobilien.derstandard.at/detail/90000001"
    assert first.title == "Sonnige 3-Zimmer-Wohnung mit 8m² Balkon"
    assert first.price == 1250.5
    assert first.price_is_net is False
    # The "8m²" in the title must not be taken for the flat's size.
    assert first.size_m2 == 68.4
    assert first.rooms == 3.0
    assert first.postcode == "1020"
    assert first.address == "Beispielstraße, 1020 Wien"
    # Gallery photos only; the agency logo is ignored.
    assert first.photos == (
        "https://img.example.com/listing/1.jpg/~/ac/format:jpg/rs:fill:335:223:1",
        "https://img.example.com/listing/2.jpg/~/ad/format:jpg/rs:fill:335:223:1",
    )


def test_derstandard_card_without_price():
    second = get_adapter("derstandard").parse(
        fixture_html("derstandard_search.html"), "rent", "flat"
    )[1]
    assert second.price is None
    assert second.size_m2 == 41.0
    assert second.rooms == 1.5
    assert second.postcode == "1160"


# --------------------------------------------------------------------------
# Shared behaviour
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "expected_id"),
    [("immowelt", "0a1b2c3d-0000-4000-8000-0000000000aa"), ("derstandard", "90000055")],
)
def test_json_ld_fallback_after_redesign(name, expected_id):
    listings = get_adapter(name).parse(fixture_html("jsonld_only.html"), "rent", "flat")
    assert [item.source_id for item in listings] == [expected_id]
    assert listings[0].size_m2 in (72.0, 88.0)


@pytest.mark.parametrize("name", ["immowelt", "derstandard"])
@pytest.mark.parametrize(
    "html", ["", "<html><body>Wartungsarbeiten</body></html>", "\x00garbage<<<"]
)
def test_empty_or_garbage_pages(name, html):
    assert get_adapter(name).parse(html, "rent", "flat") == []


@pytest.mark.parametrize("name", ["immowelt", "derstandard"])
def test_deal_and_kind_are_passed_through(name):
    fixture = "immowelt_search.html" if name == "immowelt" else "derstandard_search.html"
    listings = get_adapter(name).parse(fixture_html(fixture), "buy", "house")
    assert listings
    assert {(item.deal, item.kind) for item in listings} == {("buy", "house")}
    assert not any(item.price_is_estimate for item in listings)
