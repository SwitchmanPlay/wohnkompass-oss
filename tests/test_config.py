from pathlib import Path

import pytest

from wohnkompass_oss.config import ConfigError, load_settings

MINIMAL = '[search]\ncity = "wien"\n'


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "search.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_example_config_loads(example_toml):
    settings = load_settings(example_toml, {})
    assert settings.search.city == "wien"
    assert settings.search.deal == "rent"
    assert settings.search.types == ("flat",)
    assert settings.search.price_max == 1200
    assert settings.portals == ("immowelt", "derstandard")
    assert settings.poll_minutes == 20
    assert settings.ai.enabled is False
    assert settings.language == "en"


def test_minimal_config_uses_defaults(tmp_path):
    settings = load_settings(write(tmp_path, MINIMAL), {})
    assert settings.poll_minutes == 20
    assert settings.search.price_max is None
    assert settings.search.postcodes == ()
    assert settings.user_agent.startswith("Mozilla/5.0")
    assert settings.telegram_token is None


def test_env_values_are_read(tmp_path):
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "TELEGRAM_CHAT_ID": "42",
        "HTTP_PROXY_URL": "http://proxy.local:8080",
        "USER_AGENT": "my-agent/1.0",
    }
    settings = load_settings(write(tmp_path, MINIMAL), env)
    assert settings.telegram_token == "123:abc"
    assert settings.chat_id == 42
    assert settings.proxy_url == "http://proxy.local:8080"
    assert settings.user_agent == "my-agent/1.0"


def test_empty_env_values_count_as_unset(tmp_path):
    settings = load_settings(write(tmp_path, MINIMAL), {"USER_AGENT": "", "HTTP_PROXY_URL": " "})
    assert settings.user_agent.startswith("Mozilla/5.0")
    assert settings.proxy_url is None


def test_poll_interval_below_minimum_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="poll_minutes"):
        load_settings(write(tmp_path, "poll_minutes = 5\n" + MINIMAL), {})


@pytest.mark.parametrize(
    ("snippet", "key"),
    [
        ('city = "berlin"', "search.city"),
        ('city = "wien"\ndeal = "lease"', "search.deal"),
        ('city = "wien"\ntypes = ["castle"]', "search.types"),
        ('city = "wien"\ntypes = []', "search.types"),
        ('city = "wien"\npostcodes = ["10700"]', "search.postcodes"),
        ('city = "wien"\nexclude_red_flags = ["mould"]', "search.exclude_red_flags"),
        ('city = "wien"\nprice_max = -5', "search.price_max"),
        ('city = "wien"\nprice_min = 900\nprice_max = 500', "search.price_min"),
        ('city = "wien"\nrooms = 2', "search.rooms"),
    ],
)
def test_invalid_search_values_name_the_key(tmp_path, snippet, key):
    with pytest.raises(ConfigError, match=key.replace(".", r"\.")):
        load_settings(write(tmp_path, f"[search]\n{snippet}\n"), {})


def test_missing_search_section(tmp_path):
    with pytest.raises(ConfigError, match=r"search\.city"):
        load_settings(write(tmp_path, "poll_minutes = 20\n"), {})


def test_unknown_top_level_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="poll_minute"):
        load_settings(write(tmp_path, "poll_minute = 20\n" + MINIMAL), {})


def test_unknown_portal_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="portals"):
        load_settings(write(tmp_path, 'portals = ["willhaben"]\n' + MINIMAL), {})


def test_bad_language_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="language"):
        load_settings(write(tmp_path, 'language = "fr"\n' + MINIMAL), {})


def test_ai_enabled_requires_endpoint(tmp_path):
    text = MINIMAL + "[ai]\nenabled = true\n"
    with pytest.raises(ConfigError, match="AI_API_KEY"):
        load_settings(write(tmp_path, text), {"AI_BASE_URL": "https://x.test/v1"})
    settings = load_settings(
        write(tmp_path, text),
        {"AI_BASE_URL": "https://x.test/v1", "AI_API_KEY": "k", "AI_MODEL": "m"},
    )
    assert settings.ai.enabled is True


def test_min_score_range(tmp_path):
    with pytest.raises(ConfigError, match=r"ai\.min_score"):
        load_settings(write(tmp_path, MINIMAL + "[ai]\nmin_score = 11\n"), {})


def test_bad_chat_id_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="TELEGRAM_CHAT_ID"):
        load_settings(write(tmp_path, MINIMAL), {"TELEGRAM_CHAT_ID": "me"})


def test_require_telegram(tmp_path):
    settings = load_settings(write(tmp_path, MINIMAL), {})
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        settings.require_telegram()


def test_missing_file_and_bad_toml(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "missing.toml", {})
    with pytest.raises(ConfigError, match="TOML"):
        load_settings(write(tmp_path, "city = = 1"), {})


def test_database_path_is_relative_to_config(tmp_path):
    settings = load_settings(write(tmp_path, 'database = "db/x.sqlite3"\n' + MINIMAL), {})
    assert settings.db_path == tmp_path / "db" / "x.sqlite3"
