"""The spec cache — a tiny SQLite-backed store of known-good extraction specs.

One row per (url, target_name), holding the generated `ExtractionSpec`, the structural
`fingerprint` of the page it was generated from, and provenance (model, timestamps,
run_id, tokens). On a live run the pipeline looks up the entry: if the freshly-fetched
page's fingerprint matches, the cached spec is reused and the LLM is skipped entirely.

SQLite is used (stdlib `sqlite3`, no server, a single file) behind the `SpecCacheRepo`
interface, so a Phase-4 Postgres implementation can drop in without touching callers.

A cache must never break a scrape: any read error or corrupt/old-schema row is treated as
a miss (returns `None`) rather than raising.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from ..models.spec import ExtractionSpec

TABLE = "spec_cache"


class CacheEntry(BaseModel):
    spec: ExtractionSpec
    fingerprint: str
    model: str
    url: str
    target_name: str
    created_at: str  # ISO 8601; when the spec was first generated
    last_seen_at: str  # ISO 8601; updated on every cache hit
    run_id: str  # the run that produced this spec (-> runs/<run_id> for the page)
    tokens_in: int = 0
    tokens_out: int = 0


def cache_key(url: str, target_name: str) -> str:
    return hashlib.sha256(f"{url}\x00{target_name}".encode("utf-8")).hexdigest()[:16]


@runtime_checkable
class SpecCacheRepo(Protocol):
    """The cache seam. Phase 4 can supply a Postgres-backed implementation."""

    def get(self, url: str, target_name: str) -> CacheEntry | None: ...

    def put(self, entry: CacheEntry) -> None: ...


_SCHEMA = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    key TEXT PRIMARY KEY,
    url TEXT,
    target_name TEXT,
    fingerprint TEXT,
    model TEXT,
    spec_json TEXT,
    created_at TEXT,
    last_seen_at TEXT,
    run_id TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER
)"""

_COLUMNS = (
    "spec_json, fingerprint, model, url, target_name, "
    "created_at, last_seen_at, run_id, tokens_in, tokens_out"
)


class SqliteSpecCache:
    """SQLite implementation of `SpecCacheRepo`. Short-lived connection per call (CLI)."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        if self.db_path.parent != Path(""):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(_SCHEMA)
            con.commit()
        finally:
            con.close()

    def get(self, url: str, target_name: str) -> CacheEntry | None:
        try:
            con = sqlite3.connect(self.db_path)
            try:
                row = con.execute(
                    f"SELECT {_COLUMNS} FROM {TABLE} WHERE key = ?",
                    (cache_key(url, target_name),),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            (
                spec_json, fingerprint, model, url_, target_, created_at,
                last_seen_at, run_id, tokens_in, tokens_out,
            ) = row
            return CacheEntry(
                spec=ExtractionSpec.model_validate_json(spec_json),
                fingerprint=fingerprint,
                model=model,
                url=url_,
                target_name=target_,
                created_at=created_at,
                last_seen_at=last_seen_at,
                run_id=run_id,
                tokens_in=tokens_in or 0,
                tokens_out=tokens_out or 0,
            )
        except Exception:
            # Corrupt / old-schema row — treat as a miss; never break a scrape.
            return None

    def put(self, entry: CacheEntry) -> None:
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                f"INSERT OR REPLACE INTO {TABLE} "
                "(key, url, target_name, fingerprint, model, spec_json, "
                "created_at, last_seen_at, run_id, tokens_in, tokens_out) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cache_key(entry.url, entry.target_name),
                    entry.url,
                    entry.target_name,
                    entry.fingerprint,
                    entry.model,
                    entry.spec.model_dump_json(),
                    entry.created_at,
                    entry.last_seen_at,
                    entry.run_id,
                    entry.tokens_in,
                    entry.tokens_out,
                ),
            )
            con.commit()
        finally:
            con.close()
