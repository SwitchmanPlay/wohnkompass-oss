import pytest

from wohnkompass_oss.adapters import base


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("€ 1.072,14", 1072.14),
        ("1.100 €", 1100.0),
        ("551.47 €", 551.47),
        ("€ 850", 850.0),
        ("ab € 1.795,32", 1795.32),
        ("1.149.000 €", 1_149_000.0),
        ("Preis auf Anfrage", None),
        ("", None),
        (None, None),
        (720, 720.0),
    ],
)
def test_parse_euro(text, expected):
    assert base.parse_euro(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("71 m²", 71.0), ("148.19", 148.19), ("42,75", 42.75), ("3", 3.0), ("-", None)],
)
def test_parse_decimal(text, expected):
    assert base.parse_decimal(text) == expected


def test_clean_text_strips_tags_and_entities():
    assert base.clean_text("<b>Helle&nbsp;Wohnung</b>\n <i>mit</i>  Balkon") == (
        "Helle Wohnung mit Balkon"
    )


def test_postcode_prefers_address_and_skips_years():
    assert base.postcode_from_text("1070 Wien, Neubaugasse") == "1070"
    # "Erstbezug 2026" must not become a postcode ...
    assert base.postcode_from_text("Erstbezug 2026 in 8010 Graz") == "8010"
    assert base.postcode_from_text("Baujahr 1905") is None
    # ... but Vienna districts look like years and are real postcodes.
    assert base.postcode_from_text(None, "Wohnung in 1100 Wien") == "1100"


JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">{"@type": "WebSite", "name": "Portal"}</script>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
  {"@type": "ListItem", "item": {"@type": "Apartment", "name": "Ruhige Altbauwohnung",
   "url": "/detail/111", "floorSize": {"value": 64}, "numberOfRooms": 3,
   "address": {"postalCode": "1080", "addressLocality": "Wien"},
   "offers": {"@type": "Offer", "price": "1.150"},
   "image": ["https://example.com/a.jpg", "https://example.com/b.jpg"]}},
  {"@type": "ListItem", "item": {"@type": "Offer", "name": "Kleine Wohnung", "price": 690,
   "url": "https://portal.example/detail/222"}}
]}
</script>
<script type="application/ld+json">{ broken json </script>
</head></html>
"""


def test_jsonld_listings():
    listings = base.jsonld_listings(
        JSONLD_PAGE,
        source="derstandard",
        base_url="https://portal.example",
        id_pattern=r"/detail/(\d+)",
        deal="rent",
        kind="flat",
    )
    assert [item.source_id for item in listings] == ["111", "222"]
    first = listings[0]
    assert first.url == "https://portal.example/detail/111"
    assert first.title == "Ruhige Altbauwohnung"
    assert first.price == 1150.0
    assert first.size_m2 == 64.0
    assert first.rooms == 3.0
    assert first.postcode == "1080"
    assert first.photos == ("https://example.com/a.jpg", "https://example.com/b.jpg")
    assert listings[1].price == 690.0


def test_jsonld_listings_without_data():
    assert base.jsonld_listings("<html></html>", "x", "https://x", r"/(\d+)", "rent", "flat") == []


def test_short_title_cuts_at_a_word():
    long = (
        "Das hochwertige Neubauprojekt umfasst sieben Wohnungen und bietet unterschiedliche "
        "Wohnkonzepte für Singles, Paare und kleine Familien."
    )
    title = base.short_title(long)
    assert len(title) <= 101
    assert title.endswith("…")
    assert title.startswith("Das hochwertige Neubauprojekt")
    assert base.short_title("Kurzer Titel") == "Kurzer Titel"
