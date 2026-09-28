from pathlib import Path

import pytest

from conftest import ROOT, fixture_html
from wohnkompass_oss import cli


@pytest.fixture
def config(tmp_path) -> Path:
    text = (ROOT / "search.example.toml").read_text(encoding="utf-8")
    path = tmp_path / "search.toml"
    path.write_text(text.replace("price_max = 1200", "price_max = 2000"), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "AI_API_KEY", "SEARCH_CONFIG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)


def test_check_config_ok(config, capsys):
    assert cli.main(["--config", str(config), "check-config"]) == 0
    out = capsys.readouterr().out
    assert "Wien" in out
    assert "immowelt" in out
    assert "Telegram: not configured" in out


def test_check_config_error(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('[search]\ncity = "berlin"\n', encoding="utf-8")
    assert cli.main(["--config", str(bad), "check-config"]) == 2
    assert "search.city" in capsys.readouterr().err


def test_run_needs_telegram(config, capsys):
    assert cli.main(["--config", str(config), "run"]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_once_needs_telegram_unless_dry_run(config, capsys):
    assert cli.main(["--config", str(config), "once"]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


class FixtureClient:
    """Serves the saved fixture pages instead of the internet."""

    def __init__(self, *args, **kwargs):
        self.urls: list[str] = []

    async def get(self, url: str) -> str:
        self.urls.append(url)
        if "immowelt" in url:
            return fixture_html("immowelt_search.html")
        return fixture_html("derstandard_search.html")

    async def aclose(self) -> None:
        pass


def test_once_dry_run_prints_matches(config, capsys, monkeypatch):
    monkeypatch.setattr(cli, "PoliteClient", FixtureClient)
    assert cli.main(["--config", str(config), "once", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Helle Altbauwohnung nahe der U3." in out
    assert "Sonnige 3-Zimmer-Wohnung mit 8m² Balkon" in out
    # A dry run never adds listings to the real database.
    from wohnkompass_oss.store import Store

    real = Store(config.parent / "wohnkompass.sqlite3")
    assert real.count() == 0
    real.close()


def test_dry_run_respects_and_keeps_real_pauses(config, capsys, monkeypatch):
    from wohnkompass_oss.store import Store

    real = Store(config.parent / "wohnkompass.sqlite3")
    real.set_pause("immowelt", until=9e12, strikes=1, reason="HTTP 429")
    real.close()
    client = FixtureClient()
    monkeypatch.setattr(cli, "PoliteClient", lambda *a, **k: client)
    assert cli.main(["--config", str(config), "once", "--dry-run"]) == 0
    assert not any("immowelt" in url for url in client.urls)


def test_dry_run_saves_a_new_block(config, monkeypatch):
    from wohnkompass_oss.http import Blocked
    from wohnkompass_oss.store import Store

    class BlockingClient(FixtureClient):
        async def get(self, url):
            if "immowelt" in url:
                raise Blocked("www.immowelt.at", "HTTP 403")
            return await super().get(url)

    monkeypatch.setattr(cli, "PoliteClient", BlockingClient)
    assert cli.main(["--config", str(config), "once", "--dry-run"]) == 0
    real = Store(config.parent / "wohnkompass.sqlite3")
    assert real.portal_state("immowelt").strikes == 1
    assert not real.has_rows("derstandard")  # listings stay out of the real pool
    real.close()
