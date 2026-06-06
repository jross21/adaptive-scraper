"""Crawl orchestration — pagination + list→detail enrichment.

The single-page interpreter (`execute.interpreter.interpret`) stays pure: this layer wraps
it in iteration. Page 1 is fetched and codegen'd by the caller (so the spec cache /
fingerprint decision happens on page 1 exactly as before); the crawler then follows the
spec's `next_page_selector` to subsequent pages and reuses the SAME spec on each — so a
multi-page crawl costs **no extra LLM tokens**. Optionally it enriches each row from its
own detail page.

Live fetching and offline replay both flow through the `PageSource` seam, so a replayed
crawl walks the identical path. A failed fetch mid-crawl propagates (retry/backoff is a
later concern); the run records what it got.

Accepted v1 limitations (same spirit as the A/B note in compress/fingerprint.py): the spec
is generated from page 1 only, so a page 2+ whose structure differs surfaces as a degraded
null-rate / failed validation rather than per-page re-codegen; detail-page structure is not
fingerprinted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx
import parsel

from ..config import CRAWL_DELAY, MAX_DETAIL_PAGES, MAX_PAGES, REQUEST_TIMEOUT, USER_AGENT
from ..execute.interpreter import _resolve_dom, interpret
from ..models.spec import ExtractionSpec, FieldRule
from ..models.target import ExtractionTarget
from .fetch import FetchResult, RobotsDisallowedError, _robots_allows, fetch


class PageSource(Protocol):
    """Supplies pages 2..N (and detail pages). `get` returns (html, final_url)."""

    def get(self, url: str) -> tuple[str, str]: ...


class ReplayPageSource:
    """A `PageSource` that serves pages from a stored crawl manifest, so `--from-snapshot`
    re-walks the identical path offline. URLs not in the manifest raise KeyError."""

    def __init__(self, run_dir: Path | str, manifest: dict):
        self.run_dir = Path(run_dir)
        entries = manifest.get("pages", []) + manifest.get("detail", [])
        self._file_by_url = {e["url"]: e["file"] for e in entries}

    def get(self, url: str) -> tuple[str, str]:
        html = (self.run_dir / self._file_by_url[url]).read_text(encoding="utf-8")
        return html, url


class LivePageSource:
    """A polite live `PageSource`: one shared httpx client, a delay between requests, and a
    robots.txt check ONCE per domain (vs `fetch`'s per-call check). Every fetched page is
    retained in `.fetched` so the caller can snapshot it after the crawl."""

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = REQUEST_TIMEOUT,
        delay: float = CRAWL_DELAY,
        respect_robots: bool = True,
    ):
        self.user_agent = user_agent
        self.delay = delay
        self.respect_robots = respect_robots
        self._client = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True
        )
        self._robots_ok: dict[str, bool] = {}  # netloc -> allowed (checked once per domain)
        self.fetched: dict[str, FetchResult] = {}

    def get(self, url: str) -> tuple[str, str]:
        if self.delay:
            time.sleep(self.delay)
        if self.respect_robots:
            netloc = urlsplit(url).netloc
            if netloc not in self._robots_ok:
                self._robots_ok[netloc] = _robots_allows(self._client, url, self.user_agent)
            if not self._robots_ok[netloc]:
                raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")
        result = fetch(url, client=self._client, respect_robots=False, user_agent=self.user_agent)
        self.fetched[result.requested_url] = result
        return result.html, result.url

    def close(self) -> None:
        self._client.close()


@dataclass
class CrawlResult:
    rows: list[dict]
    pages_crawled: int
    detail_pages_fetched: int
    page_urls: list[str] = field(default_factory=list)
    detail_urls: list[str] = field(default_factory=list)


def follow_next_link(html: str, current_url: str | None, spec: ExtractionSpec) -> str | None:
    """Resolve the single 'next page' URL to absolute, or None if absent. Reuses the
    interpreter's DOM resolution and urljoin (same as the abs_url transform)."""
    if not spec.next_page_selector:
        return None
    rule = FieldRule(
        field="__next__",
        selector=spec.next_page_selector,
        selector_type=spec.next_page_selector_type,
        attribute=spec.next_page_attribute,
    )
    raw = _resolve_dom(parsel.Selector(html), rule)
    if not raw:
        return None
    return urljoin(current_url or "", raw)


def crawl(
    *,
    spec: ExtractionSpec,
    target: ExtractionTarget,
    start_html: str,
    start_url: str | None,
    page_source: PageSource,
    max_pages: int = MAX_PAGES,
    max_detail: int = MAX_DETAIL_PAGES,
    enrich_detail: bool = False,
) -> CrawlResult:
    """Walk pagination from an already-fetched page 1, accumulating rows, then optionally
    enrich each row from its detail page. Validation is the caller's job (over the merged set)."""
    rows: list[dict] = []
    page_urls: list[str] = []
    seen: set[str] = set()
    html, url = start_html, start_url

    while True:
        rows.extend(interpret(spec, html, base_url=url, cardinality=target.cardinality))
        page_urls.append(url or "")
        if url:
            seen.add(url)
        if len(page_urls) >= max(1, max_pages):
            break
        nxt = follow_next_link(html, url, spec)
        if nxt is None or nxt in seen:
            break
        html, url = page_source.get(nxt)

    detail_urls: list[str] = []
    if enrich_detail and spec.detail_url_field and spec.detail_field_rules:
        detail_urls = _enrich(rows, spec, page_source, max_detail)

    return CrawlResult(
        rows=rows,
        pages_crawled=len(page_urls),
        detail_pages_fetched=len(detail_urls),
        page_urls=page_urls,
        detail_urls=detail_urls,
    )


def _enrich(
    rows: list[dict], spec: ExtractionSpec, page_source: PageSource, max_detail: int
) -> list[str]:
    """For each row with a detail URL, fetch the detail page, extract `detail_field_rules`
    (cardinality 'one'), and fill gaps in the row (list-page values win). Returns the
    detail URLs fetched, in order."""
    detail_spec = ExtractionSpec(field_rules=spec.detail_field_rules)
    fetched: list[str] = []
    for row in rows:
        if len(fetched) >= max_detail:
            break
        durl = row.get(spec.detail_url_field)
        if not durl:
            continue
        dhtml, dfinal = page_source.get(durl)
        detail_row = interpret(detail_spec, dhtml, base_url=dfinal, cardinality="one")[0]
        for key, value in detail_row.items():
            row.setdefault(key, value)  # list-page field wins; detail fills gaps
        fetched.append(durl)
    return fetched
