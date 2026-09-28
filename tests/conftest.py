from __future__ import annotations

from pathlib import Path

import pytest

from wohnkompass_oss.models import Listing

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_listing(**overrides) -> Listing:
    data = {
        "source": "immowelt",
        "source_id": "abc-1",
        "url": "https://www.immowelt.at/expose/abc-1",
        "title": "Helle 2-Zimmer-Wohnung mit Balkon",
        "deal": "rent",
        "kind": "flat",
        "price": 950.0,
        "size_m2": 55.0,
        "rooms": 2.0,
        "postcode": "1070",
        "address": "Neubau, Wien",
        "description": "Schöne Wohnung, unbefristet, provisionsfrei.",
    }
    data.update(overrides)
    return Listing(**data)


@pytest.fixture
def example_toml() -> Path:
    return ROOT / "search.example.toml"


@pytest.fixture
def listing() -> Listing:
    return make_listing()
