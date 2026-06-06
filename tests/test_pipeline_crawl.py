"""Crawl wiring in run_pipeline: a paginating spec walks all pages, and a cache HIT does it
with zero LLM calls. Validation runs once over the merged set. No page_source => unchanged.
"""

from types import SimpleNamespace

from adaptive_scraper.cache.cache import CacheEntry, SqliteSpecCache
from adaptive_scraper.compress.compressor import compress
from adaptive_scraper.compress.fingerprint import fingerprint
from adaptive_scraper.models.schema import TARGET_REGISTRY
from adaptive_scraper.models.spec import ExtractionSpec, FieldRule
from adaptive_scraper.pipeline import run_pipeline

TARGET = TARGET_REGISTRY["job_postings"]
BASE = "https://board.example"
P1 = f"{BASE}/jobs?page=1"
P2 = f"{BASE}/jobs?page=2"
P3 = f"{BASE}/jobs?page=3"


def _page(titles, *, next_url=None):
    cards = "".join(
        f'<li class="job-card"><h3 class="job-title">{t}</h3>'
        f'<span class="company">Acme</span>'
        f'<a class="job-link" href="/jobs/{t}">apply</a></li>'
        for t in titles
    )
    nxt = f'<a class="next" href="{next_url}">Next</a>' if next_url else ""
    return f"<html><body><ul class='jobs'>{cards}</ul>{nxt}</body></html>"


def _spec():
    return ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(field="title", selector="h3.job-title", selector_type="css", transform="trim"),
            FieldRule(field="company", selector="span.company", selector_type="css", transform="trim"),
            FieldRule(field="url", selector="a.job-link", selector_type="css", attribute="href", transform="abs_url"),
        ],
        next_page_selector="a.next",
        next_page_attribute="href",
        confidence=0.95,
    )


class FakeMessages:
    def __init__(self, spec):
        self._spec = spec
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed_output=self._spec,
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


class FakeClient:
    def __init__(self, spec):
        self.messages = FakeMessages(spec)


class DictPageSource:
    def __init__(self, pages):
        self.pages = pages
        self.gets: list[str] = []

    def get(self, url):
        self.gets.append(url)
        return self.pages[url], url


def _pages():
    return {
        P2: _page(["B1", "B2", "B3"], next_url=P3),
        P3: _page(["C1", "C2", "C3"]),
    }


def test_cache_hit_reuses_paginating_spec_with_zero_llm(tmp_path):
    page1 = _page(["A1", "A2", "A3"], next_url=P2)
    cache = SqliteSpecCache(tmp_path / "cache.db")
    cache.put(
        CacheEntry(
            spec=_spec(), fingerprint=fingerprint(compress(page1)), model="claude-sonnet-4-6",
            url=P1, target_name="job_postings",
            created_at="2026-01-01T00:00:00+00:00", last_seen_at="2026-01-01T00:00:00+00:00",
            run_id="seed",
        )
    )
    client = FakeClient(_spec())

    outcome = run_pipeline(
        html=page1, base_url=P1, target=TARGET, target_name="job_postings", run_id="r",
        client=client, cache=cache, page_source=DictPageSource(_pages()), max_pages=10,
    )

    assert outcome.run.cache_status == "hit"
    assert client.messages.calls == []  # the headline: full crawl, zero LLM calls
    assert outcome.run.tokens_in == 0 and outcome.run.tokens_out == 0
    assert outcome.run.pages_crawled == 3
    assert outcome.validation.valid_count == 9  # validated once over the merged set
    assert outcome.validation.ok


def test_miss_generates_then_crawls_and_records_pages(tmp_path):
    page1 = _page(["A1", "A2", "A3"], next_url=P2)
    cache = SqliteSpecCache(tmp_path / "cache.db")
    client = FakeClient(_spec())

    outcome = run_pipeline(
        html=page1, base_url=P1, target=TARGET, target_name="job_postings", run_id="r",
        client=client, cache=cache, page_source=DictPageSource(_pages()), max_pages=10,
    )

    assert outcome.run.cache_status == "miss"
    assert len(client.messages.calls) == 1  # codegen once on page 1...
    assert outcome.run.pages_crawled == 3  # ...then the cached spec walks the rest
    assert outcome.validation.valid_count == 9
    assert cache.get(P1, "job_postings") is not None  # spec stored for next time


def test_single_page_unchanged_when_no_page_source(tmp_path):
    page1 = _page(["A1", "A2", "A3"], next_url=P2)
    cache = SqliteSpecCache(tmp_path / "cache.db")
    client = FakeClient(_spec())

    outcome = run_pipeline(
        html=page1, base_url=P1, target=TARGET, target_name="job_postings", run_id="r",
        client=client, cache=cache,  # no page_source => single page, today's behavior
    )

    assert outcome.run.pages_crawled == 1
    assert outcome.validation.valid_count == 3
    assert outcome.run.cache_status == "miss"
