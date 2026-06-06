"""Pipeline orchestration — the seam that wires the modules together.

Kept separate from the CLI so the whole loop is testable offline: pass a `spec` to
skip codegen (deterministic spine), or omit it to generate one via the LLM. Codegen runs
through the self-heal loop (`generate_spec_with_repair`); this module supplies the
"definition of success" — interpret → validate — as the evaluator callback.

Phase 3: before generating, a structural fingerprint of the page is looked up in the spec
cache. A matching fingerprint with a still-valid cached spec is a cache **hit** — the spec
is reused and the LLM is skipped entirely. A changed fingerprint (or a cached spec that no
longer validates) is **drift** — regenerate and refresh the cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic

from .cache.cache import CacheEntry, SpecCacheRepo, SqliteSpecCache
from .codegen.agent import DEFAULT_MODEL, generate_spec_with_repair
from .compress.compressor import compress
from .compress.fingerprint import fingerprint
from .config import CACHE_DB, TOKEN_BUDGET
from .execute.interpreter import interpret
from .models.run import ScrapeRun
from .models.spec import ExtractionSpec
from .models.target import ExtractionTarget
from .validate.validator import ValidationResult, validate


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PipelineOutcome:
    run: ScrapeRun
    validation: ValidationResult
    rows: list[dict]


def run_pipeline(
    *,
    html: str,
    base_url: str | None,
    target: ExtractionTarget,
    target_name: str,
    run_id: str,
    model: str = DEFAULT_MODEL,
    spec: ExtractionSpec | None = None,
    client: anthropic.Anthropic | None = None,
    snapshot_uri: str | None = None,
    rendered: bool = False,
    use_cache: bool = True,
    refresh: bool = False,
    cache: SpecCacheRepo | None = None,
) -> PipelineOutcome:
    tokens_in = tokens_out = 0
    attempts = 1
    repair_log: list[str] = []
    cache_status = "disabled"
    fingerprint_value: str | None = None

    # The cache/codegen block only runs when no spec was supplied. Passing `spec=` (replay
    # from a snapshot) bypasses it entirely, so the cache is never consulted on replay.
    if spec is None:
        compressed = compress(html, token_budget=TOKEN_BUDGET)
        fingerprint_value = fingerprint(compressed)

        def _evaluate(candidate: ExtractionSpec) -> ValidationResult:
            rows = interpret(
                candidate, html, base_url=base_url, cardinality=target.cardinality
            )
            return validate(rows, target)

        repo: SpecCacheRepo | None = None
        entry: CacheEntry | None = None
        if use_cache and base_url:
            repo = cache if cache is not None else SqliteSpecCache(CACHE_DB)
            entry = repo.get(base_url, target_name)

        if not use_cache:
            cache_status = "disabled"
        elif entry is None:
            cache_status = "miss"
        elif refresh or entry.fingerprint != fingerprint_value:
            cache_status = "drift_regenerated"  # forced, or structural drift
        elif _evaluate(entry.spec).ok:
            cache_status = "hit"  # same structure, cached spec still validates
        else:
            cache_status = "drift_regenerated"  # content/soft drift — spec stopped working

        if cache_status == "hit":
            spec = entry.spec
            model = entry.model
            entry.last_seen_at = _now_iso()
            repo.put(entry)
        else:
            repair = generate_spec_with_repair(
                compressed,
                target,
                evaluate=_evaluate,
                client=client,
                model=model,
            )
            spec = repair.spec
            model = repair.model
            tokens_in, tokens_out = repair.tokens_in, repair.tokens_out
            attempts = repair.attempts
            repair_log = repair.repair_log
            # Only cache a spec that actually works — never poison the cache with a bad one.
            if use_cache and base_url and repo is not None and repair.ok:
                now = _now_iso()
                repo.put(
                    CacheEntry(
                        spec=spec,
                        fingerprint=fingerprint_value,
                        model=model,
                        url=base_url,
                        target_name=target_name,
                        created_at=entry.created_at if entry is not None else now,
                        last_seen_at=now,
                        run_id=run_id,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                )

    rows = interpret(spec, html, base_url=base_url, cardinality=target.cardinality)
    validation = validate(rows, target)

    run = ScrapeRun(
        run_id=run_id,
        url=base_url or "",
        target_name=target_name,
        model=model,
        snapshot_uri=snapshot_uri,
        rendered=rendered,
        spec=spec,
        rows_extracted=validation.valid_count,
        final_status="ok" if validation.ok else "failed",
        validation_errors=validation.errors,
        attempts=attempts,
        repair_log=repair_log,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_status=cache_status,
        fingerprint=fingerprint_value,
    )
    return PipelineOutcome(run=run, validation=validation, rows=rows)
