"""The crawl layer: follow pagination and enrich rows from detail pages.

Driven entirely offline with a DictPageSource (a {url: html} map) so the walk's control
flow is tested without the network. interpret() stays pure; the crawler calls it per page.
"""

from adaptive_scraper.models.schema import TARGET_REGISTRY
from adaptive_scraper.models.spec import ExtractionSpec, FieldRule
from adaptive_scraper.recon.crawler import crawl, follow_next_link

TARGET = TARGET_REGISTRY["job_postings"]
BASE = "https://board.example"


class DictPageSource:
    def __init__(self, pages):
        self.pages = pages
        self.gets: list[str] = []

    def get(self, url):
        self.gets.append(url)
        return self.pages[url], url


def _listing(titles, *, next_url=None, links=None):
    cards = []
    for i, t in enumerate(titles):
        href = (links or {}).get(t)
        link = f'<a class="apply" href="{href}">apply</a>' if href else ""
        cards.append(f'<li class="job-card"><h3>{t}</h3>{link}</li>')
    nxt = f'<a class="next" href="{next_url}">Next</a>' if next_url else ""
    return f"<html><body><ul class='jobs'>{''.join(cards)}</ul>{nxt}</body></html>"


def _paginating_spec():
    return ExtractionSpec(
        container_selector="ul.jobs li.job-card",
        field_rules=[
            FieldRule(field="title", selector="h3", selector_type="css"),
            FieldRule(field="url", selector="a.apply", selector_type="css", attribute="href", transform="abs_url"),
        ],
        next_page_selector="a.next",
        next_page_attribute="href",
    )


# --- follow_next_link ----------------------------------------------------------
def test_follow_next_link_css_resolves_relative_to_absolute():
    html = _listing(["A"], next_url="/jobs?page=2")
    spec = _paginating_spec()
    assert follow_next_link(html, f"{BASE}/jobs?page=1", spec) == f"{BASE}/jobs?page=2"


def test_follow_next_link_xpath():
    html = _listing(["A"], next_url="/p2")
    spec = ExtractionSpec(
        field_rules=[FieldRule(field="title", selector="h3", selector_type="css")],
        next_page_selector="//a[@class='next']",
        next_page_selector_type="xpath",
        next_page_attribute="href",
    )
    assert follow_next_link(html, f"{BASE}/p1", spec) == f"{BASE}/p2"


def test_follow_next_link_absent_returns_none():
    html = _listing(["A"])  # no next link
    assert follow_next_link(html, f"{BASE}/p1", _paginating_spec()) is None
    no_pager = ExtractionSpec(field_rules=[FieldRule(field="t", selector="h3", selector_type="css")])
    assert follow_next_link(html, f"{BASE}/p1", no_pager) is None


# --- crawl walk ----------------------------------------------------------------
def test_crawl_walks_three_linked_pages():
    p1 = _listing(["A1", "A2", "A3"], next_url=f"{BASE}/p2")
    p2 = _listing(["B1", "B2", "B3"], next_url=f"{BASE}/p3")
    p3 = _listing(["C1", "C2", "C3"])  # last page, no next
    src = DictPageSource({f"{BASE}/p2": p2, f"{BASE}/p3": p3})

    result = crawl(
        spec=_paginating_spec(), target=TARGET, start_html=p1, start_url=f"{BASE}/p1",
        page_source=src, max_pages=10,
    )

    assert result.pages_crawled == 3
    assert [r["title"] for r in result.rows] == ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "C3"]
    assert src.gets == [f"{BASE}/p2", f"{BASE}/p3"]


def test_crawl_respects_max_pages_and_does_not_over_fetch():
    p1 = _listing(["A1"], next_url=f"{BASE}/p2")
    p2 = _listing(["B1"], next_url=f"{BASE}/p3")
    src = DictPageSource({f"{BASE}/p2": p2, f"{BASE}/p3": "unused"})

    result = crawl(
        spec=_paginating_spec(), target=TARGET, start_html=p1, start_url=f"{BASE}/p1",
        page_source=src, max_pages=2,
    )

    assert result.pages_crawled == 2
    assert [r["title"] for r in result.rows] == ["A1", "B1"]
    assert src.gets == [f"{BASE}/p2"]  # page 3 never fetched


def test_crawl_stops_on_self_loop():
    p1 = _listing(["A1"], next_url=f"{BASE}/p2")
    p2 = _listing(["B1"], next_url=f"{BASE}/p2")  # points to itself
    src = DictPageSource({f"{BASE}/p2": p2})

    result = crawl(
        spec=_paginating_spec(), target=TARGET, start_html=p1, start_url=f"{BASE}/p1",
        page_source=src, max_pages=10,
    )

    assert result.pages_crawled == 2  # terminates, no infinite loop
    assert src.gets == [f"{BASE}/p2"]


def test_crawl_single_page_when_max_pages_one():
    p1 = _listing(["A1"], next_url=f"{BASE}/p2")
    src = DictPageSource({f"{BASE}/p2": "unused"})

    result = crawl(
        spec=_paginating_spec(), target=TARGET, start_html=p1, start_url=f"{BASE}/p1",
        page_source=src, max_pages=1,
    )

    assert result.pages_crawled == 1
    assert src.gets == []  # never follows the next link


# --- detail enrichment ---------------------------------------------------------
def _detail_spec():
    spec = _paginating_spec()
    spec.next_page_selector = None  # single list page for these tests
    spec.detail_url_field = "url"
    spec.detail_field_rules = [
        FieldRule(field="description", selector="div.description", selector_type="css"),
    ]
    return spec


def _detail_page(desc):
    return f'<html><body><div class="description">{desc}</div></body></html>'


def test_detail_enrichment_merges_fields():
    links = {"A1": f"{BASE}/job/1", "A2": f"{BASE}/job/2"}
    p1 = _listing(["A1", "A2"], links=links)
    src = DictPageSource({f"{BASE}/job/1": _detail_page("Desc 1"), f"{BASE}/job/2": _detail_page("Desc 2")})

    result = crawl(
        spec=_detail_spec(), target=TARGET, start_html=p1, start_url=f"{BASE}/jobs",
        page_source=src, enrich_detail=True,
    )

    assert result.detail_pages_fetched == 2
    descs = {r["title"]: r["description"] for r in result.rows}
    assert descs == {"A1": "Desc 1", "A2": "Desc 2"}


def test_detail_respects_max_detail():
    links = {"A1": f"{BASE}/job/1", "A2": f"{BASE}/job/2"}
    p1 = _listing(["A1", "A2"], links=links)
    src = DictPageSource({f"{BASE}/job/1": _detail_page("Desc 1"), f"{BASE}/job/2": _detail_page("Desc 2")})

    result = crawl(
        spec=_detail_spec(), target=TARGET, start_html=p1, start_url=f"{BASE}/jobs",
        page_source=src, enrich_detail=True, max_detail=1,
    )

    assert result.detail_pages_fetched == 1
    assert result.rows[0].get("description") == "Desc 1"
    assert result.rows[1].get("description") is None  # cap hit, not enriched


def test_detail_skips_rows_without_detail_url():
    p1 = _listing(["A1", "A2"], links={"A1": f"{BASE}/job/1"})  # A2 has no link
    src = DictPageSource({f"{BASE}/job/1": _detail_page("Desc 1")})

    result = crawl(
        spec=_detail_spec(), target=TARGET, start_html=p1, start_url=f"{BASE}/jobs",
        page_source=src, enrich_detail=True,
    )

    assert result.detail_pages_fetched == 1
    assert src.gets == [f"{BASE}/job/1"]  # A2 (no url) never fetched
