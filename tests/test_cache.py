"""SQLite spec cache: store a known-good ExtractionSpec keyed by URL+target, plus its
structural fingerprint. A corrupt or stale row must never break a scrape — it's a miss.
"""

import sqlite3

from adaptive_scraper.cache.cache import TABLE, CacheEntry, SqliteSpecCache, cache_key
from adaptive_scraper.models.spec import ExtractionSpec, FieldRule

URL = "https://example.com/jobs"
TARGET = "job_postings"


def _entry(**over) -> CacheEntry:
    base = dict(
        spec=ExtractionSpec(
            container_selector="ul.jobs li.job-card",
            field_rules=[FieldRule(field="title", selector="h3", selector_type="css")],
            confidence=0.9,
        ),
        fingerprint="abc1230000000000",
        model="claude-sonnet-4-6",
        url=URL,
        target_name=TARGET,
        created_at="2026-06-05T00:00:00+00:00",
        last_seen_at="2026-06-05T00:00:00+00:00",
        run_id="20260605T000000Z-aaaaaa",
        tokens_in=100,
        tokens_out=50,
    )
    base.update(over)
    return CacheEntry(**base)


def test_round_trip(tmp_path):
    cache = SqliteSpecCache(tmp_path / "cache.db")
    entry = _entry()
    cache.put(entry)
    assert cache.get(URL, TARGET) == entry


def test_miss_returns_none(tmp_path):
    cache = SqliteSpecCache(tmp_path / "cache.db")
    assert cache.get("https://nope.example", TARGET) is None


def test_put_upserts_single_row(tmp_path):
    db = tmp_path / "cache.db"
    cache = SqliteSpecCache(db)
    cache.put(_entry(last_seen_at="t1"))
    cache.put(_entry(last_seen_at="t2"))

    assert cache.get(URL, TARGET).last_seen_at == "t2"
    con = sqlite3.connect(db)
    try:
        (count,) = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()
    finally:
        con.close()
    assert count == 1


def test_cache_key_is_stable_and_unique():
    k = cache_key(URL, TARGET)
    assert k == cache_key(URL, TARGET)
    assert k != cache_key("https://example.com/other", TARGET)
    assert k != cache_key(URL, "products")


def test_corrupt_row_is_treated_as_miss(tmp_path):
    db = tmp_path / "cache.db"
    cache = SqliteSpecCache(db)  # creates the table
    con = sqlite3.connect(db)
    try:
        con.execute(
            f"INSERT INTO {TABLE} (key, spec_json) VALUES (?, ?)",
            (cache_key(URL, TARGET), "{not valid json"),
        )
        con.commit()
    finally:
        con.close()

    assert cache.get(URL, TARGET) is None  # must not raise
