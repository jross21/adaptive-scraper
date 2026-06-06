"""Phase-3 cache + drift behavior in the pipeline.

The cache's whole point is cost: on a live run, a known-good spec for an unchanged page is
reused and the LLM is NOT called. These tests drive run_pipeline offline with the committed
fixture, a fake Claude client (so we can assert it is or isn't called), and a real SQLite
cache on tmp_path.
"""

from pathlib import Path
from types import SimpleNamespace

from adaptive_scraper.cache.cache import CacheEntry, SqliteSpecCache
from adaptive_scraper.compress.compressor import compress
from adaptive_scraper.compress.fingerprint import fingerprint
from adaptive_scraper.models.schema import TARGET_REGISTRY
from adaptive_scraper.models.spec import ExtractionSpec, FieldRule
from adaptive_scraper.pipeline import run_pipeline

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://careers.acme.example/careers"
TARGET = TARGET_REGISTRY["job_postings"]
SEED_TS = "2026-01-01T00:00:00+00:00"


def _html() -> str:
    return (FIXTURES / "jobs_listing.html").read_text()


def _good_spec() -> ExtractionSpec:
    return ExtractionSpec.model_validate_json((FIXTURES / "jobs_spec.json").read_text())


def _bad_spec() -> ExtractionSpec:
    return ExtractionSpec(
        container_selector="ul.does-not-exist li",
        field_rules=[FieldRule(field="title", selector="h3", selector_type="css")],
        confidence=0.1,
    )


def _fp() -> str:
    return fingerprint(compress(_html()))


class FakeMessages:
    def __init__(self, spec):
        self._spec = spec
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed_output=self._spec,
            usage=SimpleNamespace(input_tokens=123, output_tokens=45),
        )


class FakeClient:
    def __init__(self, spec):
        self.messages = FakeMessages(spec)


class SpyCache:
    """Records get/put calls — to prove replay never touches the cache."""

    def __init__(self):
        self.gets = 0
        self.puts = 0

    def get(self, url, target_name):
        self.gets += 1
        return None

    def put(self, entry):
        self.puts += 1


def _seed(cache, *, spec, fingerprint_value):
    cache.put(
        CacheEntry(
            spec=spec,
            fingerprint=fingerprint_value,
            model="claude-sonnet-4-6",
            url=BASE_URL,
            target_name="job_postings",
            created_at=SEED_TS,
            last_seen_at=SEED_TS,
            run_id="seed",
        )
    )


def _run(html, *, cache, client, use_cache=True, refresh=False, spec=None):
    return run_pipeline(
        html=html,
        base_url=BASE_URL,
        target=TARGET,
        target_name="job_postings",
        run_id="test-run",
        client=client,
        spec=spec,
        use_cache=use_cache,
        refresh=refresh,
        cache=cache,
    )


def test_miss_generates_and_stores(tmp_path):
    cache = SqliteSpecCache(tmp_path / "cache.db")
    client = FakeClient(_good_spec())

    outcome = _run(_html(), cache=cache, client=client)

    assert outcome.run.cache_status == "miss"
    assert len(client.messages.calls) == 1  # first contact -> LLM called
    assert outcome.validation.ok
    stored = cache.get(BASE_URL, "job_postings")
    assert stored is not None
    assert stored.fingerprint == _fp()
    assert outcome.run.fingerprint == _fp()


def test_hit_reuses_spec_and_skips_llm(tmp_path):
    cache = SqliteSpecCache(tmp_path / "cache.db")
    _seed(cache, spec=_good_spec(), fingerprint_value=_fp())
    client = FakeClient(_good_spec())

    outcome = _run(_html(), cache=cache, client=client)

    assert outcome.run.cache_status == "hit"
    assert client.messages.calls == []  # the whole point: LLM skipped, $0
    assert outcome.validation.ok
    assert outcome.run.tokens_in == 0 and outcome.run.tokens_out == 0
    assert cache.get(BASE_URL, "job_postings").last_seen_at != SEED_TS  # bumped


def test_structural_drift_regenerates_and_updates(tmp_path):
    cache = SqliteSpecCache(tmp_path / "cache.db")
    _seed(cache, spec=_good_spec(), fingerprint_value="deadbeefdeadbeef")  # wrong fp
    client = FakeClient(_good_spec())

    outcome = _run(_html(), cache=cache, client=client)

    assert outcome.run.cache_status == "drift_regenerated"
    assert len(client.messages.calls) == 1  # structural drift -> regenerate
    assert cache.get(BASE_URL, "job_postings").fingerprint == _fp()  # refreshed


def test_content_drift_regenerates_when_cached_spec_stops_validating(tmp_path):
    cache = SqliteSpecCache(tmp_path / "cache.db")
    _seed(cache, spec=_bad_spec(), fingerprint_value=_fp())  # fp matches, spec is stale
    client = FakeClient(_good_spec())

    outcome = _run(_html(), cache=cache, client=client)

    assert outcome.run.cache_status == "drift_regenerated"
    assert len(client.messages.calls) == 1
    assert outcome.validation.ok
    assert (
        cache.get(BASE_URL, "job_postings").spec.container_selector
        == _good_spec().container_selector
    )


def test_no_cache_disables_lookup_and_store(tmp_path):
    cache = SqliteSpecCache(tmp_path / "cache.db")
    _seed(cache, spec=_good_spec(), fingerprint_value=_fp())  # a valid hit exists...
    client = FakeClient(_good_spec())

    outcome = _run(_html(), cache=cache, client=client, use_cache=False)

    assert outcome.run.cache_status == "disabled"
    assert len(client.messages.calls) == 1  # ...but --no-cache ignores it


def test_refresh_forces_regeneration(tmp_path):
    cache = SqliteSpecCache(tmp_path / "cache.db")
    _seed(cache, spec=_good_spec(), fingerprint_value=_fp())
    client = FakeClient(_good_spec())

    outcome = _run(_html(), cache=cache, client=client, refresh=True)

    assert outcome.run.cache_status == "drift_regenerated"
    assert len(client.messages.calls) == 1


def test_replay_with_spec_never_touches_cache():
    spy = SpyCache()
    client = FakeClient(_good_spec())

    outcome = run_pipeline(
        html=_html(),
        base_url=BASE_URL,
        target=TARGET,
        target_name="job_postings",
        run_id="replay",
        spec=_good_spec(),
        client=client,
        cache=spy,
    )

    assert outcome.run.cache_status == "disabled"
    assert outcome.run.fingerprint is None
    assert spy.gets == 0 and spy.puts == 0
    assert client.messages.calls == []  # codegen skipped entirely
