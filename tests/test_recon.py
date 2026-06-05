import httpx
import pytest
import respx

from adaptive_scraper.recon.fetch import RobotsDisallowedError, fetch
from adaptive_scraper.recon.snapshot import load_snapshot, save_snapshot

PAGE_URL = "https://jobs.example.com/listings"
ROBOTS_URL = "https://jobs.example.com/robots.txt"
PAGE_HTML = "<html><body><h1>Jobs</h1></body></html>"


@respx.mock
def test_fetch_returns_html_when_allowed():
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=PAGE_HTML))

    result = fetch(PAGE_URL)

    assert result.status_code == 200
    assert "<h1>Jobs</h1>" in result.html
    assert result.url == PAGE_URL


@respx.mock
def test_fetch_refuses_disallowed_path():
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=PAGE_HTML))

    with pytest.raises(RobotsDisallowedError):
        fetch(PAGE_URL)


@respx.mock
def test_fetch_allows_when_robots_absent():
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=PAGE_HTML))

    result = fetch(PAGE_URL)
    assert result.status_code == 200


@respx.mock
def test_fetch_can_skip_robots():
    respx.get(PAGE_URL).mock(return_value=httpx.Response(200, text=PAGE_HTML))
    result = fetch(PAGE_URL, respect_robots=False)
    assert result.status_code == 200


def test_snapshot_round_trip(tmp_path):
    result = httpx.Response(200, text=PAGE_HTML, request=httpx.Request("GET", PAGE_URL))
    # Build a FetchResult-like via the real fetch path would need network; construct directly.
    from adaptive_scraper.recon.fetch import FetchResult

    fr = FetchResult(
        url=PAGE_URL,
        requested_url=PAGE_URL,
        status_code=200,
        html=PAGE_HTML,
        fetched_at="2024-06-05T00:00:00+00:00",
        user_agent="test-agent",
    )
    path = save_snapshot("run-123", fr, runs_dir=tmp_path)
    snap = load_snapshot(path)

    assert snap.html == PAGE_HTML
    assert snap.meta["url"] == PAGE_URL
    assert snap.meta["status_code"] == 200
