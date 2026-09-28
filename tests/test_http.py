import httpx
import pytest
import respx

from wohnkompass_oss.http import (
    Blocked,
    FetchError,
    PoliteClient,
    looks_like_challenge,
    pause_seconds,
)

RESULTS_PAGE = "<html>" + "<li class='card'>Wohnung</li>" * 3000 + "</html>"


class FakeTime:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time():
    return FakeTime()


@pytest.fixture
async def client(fake_time):
    polite = PoliteClient(
        user_agent="test-agent/1.0",
        clock=fake_time.clock,
        sleep=fake_time.sleep,
        rng=lambda: 0.0,
    )
    yield polite
    await polite.aclose()


@respx.mock
async def test_sends_user_agent_and_returns_text(client):
    route = respx.get("https://a.example/search").respond(200, text=RESULTS_PAGE)
    assert await client.get("https://a.example/search") == RESULTS_PAGE
    request = route.calls.last.request
    assert request.headers["User-Agent"] == "test-agent/1.0"
    assert request.headers["Accept-Language"].startswith("de-AT")


@respx.mock
async def test_waits_between_requests_to_the_same_host(client, fake_time):
    respx.get(url__startswith="https://a.example/").respond(200, text=RESULTS_PAGE)
    await client.get("https://a.example/1")
    await client.get("https://a.example/2")
    assert fake_time.sleeps == [10.0]


@respx.mock
async def test_hosts_do_not_wait_for_each_other(client, fake_time):
    respx.get(url__startswith="https://a.example/").respond(200, text=RESULTS_PAGE)
    respx.get(url__startswith="https://b.example/").respond(200, text=RESULTS_PAGE)
    await client.get("https://a.example/1")
    await client.get("https://b.example/1")
    assert fake_time.sleeps == []


@respx.mock
async def test_jitter_is_added(fake_time):
    respx.get(url__startswith="https://a.example/").respond(200, text=RESULTS_PAGE)
    polite = PoliteClient("ua", clock=fake_time.clock, sleep=fake_time.sleep, rng=lambda: 1.0)
    await polite.get("https://a.example/1")
    await polite.get("https://a.example/2")
    await polite.aclose()
    assert fake_time.sleeps == [15.0]


@respx.mock
@pytest.mark.parametrize("status", [403, 429])
async def test_block_status_raises_blocked(client, status):
    respx.get("https://a.example/").respond(status, headers={"Retry-After": "7200"}, text="no")
    with pytest.raises(Blocked) as info:
        await client.get("https://a.example/")
    assert info.value.host == "a.example"
    assert info.value.reason == f"HTTP {status}"
    assert info.value.retry_after == 7200.0


@respx.mock
async def test_captcha_page_raises_blocked(client):
    respx.get("https://a.example/").respond(200, text="<html>Please solve the CAPTCHA</html>")
    with pytest.raises(Blocked, match="captcha"):
        await client.get("https://a.example/")


@respx.mock
async def test_empty_202_raises_blocked(client):
    respx.get("https://a.example/").respond(202, text="")
    with pytest.raises(Blocked, match="202"):
        await client.get("https://a.example/")


@respx.mock
async def test_server_error_is_retried_once_after_30s(client, fake_time):
    route = respx.get("https://a.example/")
    route.side_effect = [httpx.Response(503), httpx.Response(200, text=RESULTS_PAGE)]
    assert await client.get("https://a.example/") == RESULTS_PAGE
    assert fake_time.sleeps == [30.0]


@respx.mock
async def test_repeated_server_error_gives_up(client):
    respx.get("https://a.example/").respond(503)
    with pytest.raises(FetchError, match="503"):
        await client.get("https://a.example/")


@respx.mock
async def test_network_error_gives_up_after_one_retry(client, fake_time):
    respx.get("https://a.example/").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(FetchError, match="boom"):
        await client.get("https://a.example/")
    assert fake_time.sleeps == [30.0]


@respx.mock
async def test_not_found_is_an_error_not_a_block(client):
    respx.get("https://a.example/").respond(404)
    with pytest.raises(FetchError, match="404"):
        await client.get("https://a.example/")


def test_pause_escalation():
    assert pause_seconds(1) == 1800
    assert pause_seconds(2) == 3600
    assert pause_seconds(3) == 7200
    assert pause_seconds(10) == 86_400


@pytest.mark.parametrize(
    ("status", "text", "reason"),
    [
        (200, RESULTS_PAGE, None),
        # Large real pages may mention reCAPTCHA in a script; only short pages count.
        (200, RESULTS_PAGE + "recaptcha", None),
        (200, "<p>Bist du ein Mensch?</p>", "bot check"),
        (200, "<p>Please solve the captcha</p>", "captcha page"),
        (200, "<div id='challenge-platform'></div>", "bot check"),
        (403, "", "HTTP 403"),
        (202, "   ", "empty HTTP 202 (bot check)"),
    ],
    ids=["results", "results-with-recaptcha", "mensch", "challenge", "captcha", "403", "202"],
)
def test_looks_like_challenge(status, text, reason):
    assert looks_like_challenge(status, text) == reason
