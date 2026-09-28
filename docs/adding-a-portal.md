# Adding a portal

A portal adapter is two pure functions. Fetching, pacing, block handling, storage
and notifications are shared, so a new portal is usually one small module plus a test.

## 1. Check that you may

Before writing any code, read the portal's `robots.txt` and terms of use. If either
forbids automated access to the pages you need, stop there. willhaben.at is not
included in this project for exactly that reason.

## 2. Write the adapter

Create `src/wohnkompass_oss/adapters/<portal>.py`:

```python
from ..config import Search
from ..models import Listing
from .base import clean_text, denoise, jsonld_listings, parse_decimal, parse_euro, short_title

NAME = "myportal"
HOST = "www.myportal.at"


class MyPortalAdapter:
    name = NAME
    host = HOST

    def build_url(self, search: Search, kind: str) -> str:
        """The search page for one city, deal and kind, newest listings first."""
        ...

    def parse(self, html: str, deal: str, kind: str) -> list[Listing]:
        """Every listing on the page, or [] when there are none."""
        ...
```

Guidelines that keep adapters alive through redesigns:

- **Newest first.** The pipeline reads page 1 only, so the URL must sort by date.
- **Parse the portal's own labels** (`data-testid`, class names) rather than guessing
  from numbers near a link.
- **Fall back to `jsonld_listings()`**: most portals keep schema.org markup for search
  engines, and it survives many front-end rewrites.
- **Return `[]` rather than raising** for a page you do not understand. Three empty
  pages in a row trigger a "parser may be broken" message in Telegram.
- **Set `price_is_net=True`** when the portal quotes net rent, so alerts show an
  estimated warm rent.
- Leave out fields you cannot read (`None`); filters treat missing data sensibly.

## 3. Register it

Add the adapter to `ADAPTERS` in `src/wohnkompass_oss/adapters/__init__.py` and its name
to `PORTALS` in `config.py`.

## 4. Test it offline

Save one results page, then **replace every real detail** (names, phone numbers,
addresses, prices, image URLs) with invented ones while keeping the markup structure.
Put it in `tests/fixtures/` and assert on the parsed fields in `tests/test_adapters.py`.
Tests never touch the network.

Then run a real check once:

```bash
wohnkompass-oss once --dry-run
```
