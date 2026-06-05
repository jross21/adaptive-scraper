"""Phase-2 JS rendering: the under-rendered heuristic + the Playwright render path.

The heuristic and model-field tests run offline. The actual browser render is marked
`render` (deselected by default; needs `playwright install chromium`).
"""

from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from adaptive_scraper.models.run import ScrapeRun
from adaptive_scraper.recon.fetch import FetchResult
from adaptive_scraper.recon.render import looks_js_rendered, render_page

FIXTURES = Path(__file__).parent / "fixtures"
JS_PAGE = FIXTURES / "js_page.html"
STATIC_PAGE = FIXTURES / "jobs_listing.html"


def test_looks_js_rendered_flags_empty_spa_shell():
    # Empty <div id="root"></div> + a build script — classic unrendered SPA.
    assert looks_js_rendered(JS_PAGE.read_text()) is True


def test_looks_js_rendered_passes_real_content():
    # The static fixture already has its rows in the HTML — no rendering needed.
    assert looks_js_rendered(STATIC_PAGE.read_text()) is False


def test_fetch_result_defaults_to_not_rendered():
    fr = FetchResult(
        url="https://x.example/",
        requested_url="https://x.example/",
        status_code=200,
        html="<html></html>",
        fetched_at="2026-06-05T00:00:00+00:00",
        user_agent="ua",
    )
    assert fr.rendered is False


def test_scraperun_phase2_field_defaults():
    run = ScrapeRun(run_id="r", url="u", target_name="job_postings", model="m")
    assert run.attempts == 1
    assert run.models_tried == []
    assert run.repair_log == []
    assert run.rendered is False


@pytest.mark.render
def test_render_page_executes_javascript():
    url = JS_PAGE.resolve().as_uri()  # file:// URL; robots check skipped below
    result = render_page(url, respect_robots=False)

    assert result.rendered is True
    # The job cards are built by the page's JS, so they exist in the DOM only after
    # rendering — a static parse of the raw file finds none.
    static_cards = HTMLParser(JS_PAGE.read_text()).css("li.job-card")
    rendered_cards = HTMLParser(result.html).css("li.job-card")
    assert len(static_cards) == 0
    assert len(rendered_cards) == 3
    assert "Senior Backend Engineer" in result.html
