"""Run record — observability + replay metadata for one scrape attempt.

Stored alongside the page snapshot so a run is fully reproducible offline: replay
re-runs the stored `spec` against the stored snapshot rather than re-generating.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .spec import ExtractionSpec


class ScrapeRun(BaseModel):
    run_id: str
    url: str
    target_name: str
    model: str  # the model that produced the stored spec
    snapshot_uri: str | None = None
    rendered: bool = False  # whether the page was JS-rendered (Phase 2) rather than static
    spec: ExtractionSpec | None = None
    rows_extracted: int = 0
    final_status: Literal["ok", "failed"] = "failed"
    validation_errors: list[str] = Field(default_factory=list)
    # Self-heal observability: how many codegen attempts ran and a per-attempt log.
    # tokens_in/out accumulate across attempts.
    attempts: int = 1
    repair_log: list[str] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    # Phase-3 spec cache: how the spec was sourced, and the structural fingerprint of the
    # page. On a "hit" the cached spec was reused and the LLM was skipped (tokens stay 0).
    cache_status: Literal["miss", "hit", "drift_regenerated", "disabled"] = "disabled"
    fingerprint: str | None = None
    # Crawl observability: pages followed via pagination and detail pages fetched (1/0 for a
    # single-page run). The same spec drives every page, so these don't add LLM cost.
    pages_crawled: int = 1
    detail_pages_fetched: int = 0
    crawl_page_urls: list[str] = Field(default_factory=list)
