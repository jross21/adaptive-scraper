"""Multi-page snapshots + manifest, and replay re-walking them.

A crawl stores every fetched page + an ordered manifest so `--from-snapshot` can re-walk
the identical path offline (preserving the snapshot+spec ⇒ reproducible invariant). A run
with no manifest is a legacy single-page snapshot.
"""

from adaptive_scraper.recon.crawler import ReplayPageSource
from adaptive_scraper.recon.fetch import FetchResult
from adaptive_scraper.recon.snapshot import (
    load_manifest,
    save_crawl_page,
    save_detail_page,
    save_manifest,
)


def _fr(url, html):
    return FetchResult(
        url=url, requested_url=url, status_code=200, html=html,
        fetched_at="2026-01-01T00:00:00+00:00", user_agent="ua",
    )


def test_manifest_round_trip(tmp_path):
    manifest = {
        "version": 1,
        "pages": [{"url": "https://x/p1", "file": "page.html"}],
        "detail": [],
    }
    save_manifest(tmp_path, manifest)
    assert load_manifest(tmp_path) == manifest


def test_load_manifest_returns_none_for_legacy_run(tmp_path):
    assert load_manifest(tmp_path) is None  # no manifest.json => legacy single-page snapshot


def test_save_crawl_page_then_replay_reads_it_back(tmp_path):
    path = save_crawl_page(tmp_path, 2, _fr("https://x/p2", "<html>page two</html>"))
    assert path.exists()

    manifest = {
        "version": 1,
        "pages": [
            {"url": "https://x/p1", "file": "page.html"},
            {"url": "https://x/p2", "file": "pages/002.html"},
        ],
        "detail": [],
    }
    src = ReplayPageSource(tmp_path, manifest)
    html, url = src.get("https://x/p2")
    assert "page two" in html
    assert url == "https://x/p2"


def test_save_detail_page_then_replay_reads_it_back(tmp_path):
    path = save_detail_page(tmp_path, "https://x/job/1", _fr("https://x/job/1", "<html>detail one</html>"))
    assert path.exists()

    manifest = {
        "version": 1,
        "pages": [],
        "detail": [{"url": "https://x/job/1", "file": path.relative_to(tmp_path).as_posix()}],
    }
    src = ReplayPageSource(tmp_path, manifest)
    html, _ = src.get("https://x/job/1")
    assert "detail one" in html
