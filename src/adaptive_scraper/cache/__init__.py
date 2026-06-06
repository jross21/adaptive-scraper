"""Phase-3 spec cache: store a known-good ExtractionSpec per URL+target, keyed alongside
the page's structural fingerprint so unchanged pages reuse the spec and skip the LLM."""

from .cache import CacheEntry, SpecCacheRepo, SqliteSpecCache, cache_key

__all__ = ["CacheEntry", "SpecCacheRepo", "SqliteSpecCache", "cache_key"]
