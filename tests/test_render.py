import re

from conftest import make_listing
from wohnkompass_oss.ai import Score
from wohnkompass_oss.config import Search
from wohnkompass_oss.i18n import STRINGS, t
from wohnkompass_oss.render import format_eur, render_alert, render_search, render_status
from wohnkompass_oss.store import PortalState


def test_languages_have_the_same_keys_and_placeholders():
    en, de = STRINGS["en"], STRINGS["de"]
    assert en.keys() == de.keys()
    for key in en:
        assert set(re.findall(r"{(\w+)}", en[key])) == set(re.findall(r"{(\w+)}", de[key])), key


def test_t_formats_values():
    assert t("de", "rooms", rooms="3") == "3 Zimmer"


def test_format_eur():
    assert format_eur(1072.14, "en") == "€1,072"
    assert format_eur(1072.14, "de") == "1.072 €"
    assert format_eur(None, "en") is None


def test_alert_basic_fields():
    text = render_alert(make_listing(price=950.0, size_m2=55.0, rooms=2.0, postcode="1070"), "en")
    assert text.startswith("🏠 New")
    assert '<a href="https://www.immowelt.at/expose/abc-1">' in text
    assert "€950" in text
    assert "55 m²" in text
    assert "2 rooms" in text
    assert "1070" in text


def test_alert_escapes_html_in_listing_text():
    text = render_alert(make_listing(title="<b>Top</b> & more", address="<script>"), "en")
    assert "&lt;b&gt;Top&lt;/b&gt; &amp; more" in text
    assert "<script>" not in text


def test_alert_estimated_rent_shows_net_price():
    listing = make_listing(price=900.0, price_is_net=True, size_m2=50.0)
    text = render_alert(listing, "de")
    assert "≈ 1.080 €" in text
    assert "netto 900 €" in text


def test_alert_price_drop_flags_score_and_also_on():
    listing = make_listing(price=800.0, description="Befristet auf 3 Jahre")
    text = render_alert(
        listing,
        "en",
        score=Score(8, "Good value for Neubau."),
        also_on=["derstandard"],
        previous_price=950.0,
    )
    assert text.startswith("📉 Price drop")
    assert "was €950" in text
    assert "fixed-term lease" in text
    assert "8/10" in text
    assert "Good value for Neubau." in text
    assert "also on derStandard" in text


def test_alert_without_price():
    assert "price on request" in render_alert(make_listing(price=None), "en")


def test_render_search():
    search = Search(city="wien", price_max=1200, size_min=45, rooms_min=2, postcodes=("1070",))
    summary = render_search(search, "en")
    assert "Wien" in summary
    assert "€1,200" in summary
    assert "1070" in summary


def test_render_status():
    states = [
        ("immowelt", PortalState(last_run=1_000.0, last_count=30)),
        ("derstandard", PortalState(paused_until=10_000.0, strikes=1, reason="HTTP 429")),
    ]
    text = render_status("en", states, pool_size=42, paused=True, now=5_000.0)
    assert "immowelt" in text
    assert "30 listings" in text
    assert "derStandard: paused" in text
    assert "HTTP 429" in text
    assert "42" in text
    assert "/resume" in text
