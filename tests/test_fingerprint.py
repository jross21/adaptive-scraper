"""Structural fingerprint: a hash of a page's *shape*, not its content.

The fingerprint must be stable across content changes (new jobs, different salaries/dates/
links, different row counts) but change when the template structure changes (container
signature, JSON-LD @type shape, tag/class skeleton, nesting). It drives the Phase-3 cache:
same fingerprint => reuse the cached spec and skip the LLM; different => structural drift.
"""

import json

from adaptive_scraper.compress.compressor import compress
from adaptive_scraper.compress.fingerprint import fingerprint

JOBS_A = [
    {"title": "Senior Backend Engineer", "company": "Acme", "href": "/jobs/1",
     "date": "2026-01-01", "salary": 120000},
    {"title": "Frontend Developer", "company": "Acme", "href": "/jobs/2",
     "date": "2026-02-01", "salary": 110000},
    {"title": "Data Scientist", "company": "Acme", "href": "/jobs/3",
     "date": "2026-03-01", "salary": 130000},
]
# Different content AND a different number of rows (5 vs 3).
JOBS_B = [
    {"title": "Platform Engineer", "company": "Globex", "href": "/careers/x9",
     "date": "2025-11-15", "salary": 99000},
    {"title": "ML Engineer", "company": "Globex", "href": "/careers/x8",
     "date": "2025-10-20", "salary": 150000},
    {"title": "SRE", "company": "Globex", "href": "/careers/x7",
     "date": "2025-09-01", "salary": 140000},
    {"title": "Designer", "company": "Globex", "href": "/careers/x6",
     "date": "2025-08-12", "salary": 95000},
    {"title": "Product Manager", "company": "Globex", "href": "/careers/x5",
     "date": "2025-07-30", "salary": 125000},
]


def _jsonld(jobs, ld_type="JobPosting"):
    items = [
        {
            "@context": "https://schema.org",
            "@type": ld_type,
            "title": j["title"],
            "datePosted": j["date"],
            "baseSalary": {"@type": "MonetaryAmount", "value": j["salary"]},
        }
        for j in jobs
    ]
    return '<script type="application/ld+json">' + json.dumps(items) + "</script>"


def page(jobs, *, container_class="job-card", with_jsonld=True, ld_type="JobPosting", nested=False):
    def card(j):
        inner = (
            f'<h3>{j["title"]}</h3>'
            f'<span class="company">{j["company"]}</span>'
            f'<a class="apply" href="{j["href"]}">Apply</a>'
        )
        if nested:
            inner = f'<div class="inner">{inner}</div>'
        return f'<li class="{container_class}">{inner}</li>'

    head = _jsonld(jobs, ld_type) if with_jsonld else ""
    cards = "".join(card(j) for j in jobs)
    return (
        f"<html><head><title>Jobs</title>{head}</head>"
        f"<body><ul class='jobs'>{cards}</ul></body></html>"
    )


def fp(html):
    return fingerprint(compress(html))


def test_fingerprint_is_deterministic():
    assert fp(page(JOBS_A)) == fp(page(JOBS_A))


def test_fingerprint_ignores_content_and_row_count():
    # Same structure; different titles/companies/salaries/dates/hrefs and 3 vs 5 rows.
    assert fp(page(JOBS_A)) == fp(page(JOBS_B))


def test_fingerprint_detects_container_class_change():
    assert fp(page(JOBS_A)) != fp(page(JOBS_A, container_class="posting"))


def test_fingerprint_detects_missing_json_ld():
    assert fp(page(JOBS_A)) != fp(page(JOBS_A, with_jsonld=False))


def test_fingerprint_detects_json_ld_type_change():
    assert fp(page(JOBS_A)) != fp(page(JOBS_A, ld_type="Vacancy"))


def test_fingerprint_detects_added_nesting_level():
    assert fp(page(JOBS_A)) != fp(page(JOBS_A, nested=True))


def test_fingerprint_ignores_build_hashed_class_suffixes():
    # Per-deploy CSS-module/Tailwind hashes must not look like structural drift.
    assert fp(page(JOBS_A, container_class="css-1a2b3c")) == fp(
        page(JOBS_A, container_class="css-9f8e7d")
    )
