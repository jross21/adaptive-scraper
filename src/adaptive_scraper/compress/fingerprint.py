"""Structural fingerprint of a page — the key to the Phase-3 spec cache.

A fingerprint is a short hash of a page's *shape*: the repeating-row signatures, the
JSON-LD `@type` shape, and a normalized tag/class outline. It is deliberately
**content-insensitive** (new rows, changed salaries/dates/titles/links, and row counts do
NOT change it) and **structure-sensitive** (a changed container class, a dropped or
re-typed JSON-LD block, or a changed tag/class skeleton DO change it).

The cache compares a freshly-fetched page's fingerprint to the one stored with the cached
spec: a match means "same template, reuse the spec, skip the LLM"; a mismatch means
structural drift, so regenerate.

Known accepted limitation: a single URL that serves two genuinely different layouts (A/B
tests, multivariate) will alternate fingerprints and re-generate on each flip. That
self-heals at one codegen call per flip and isn't worth variant-set caching here.
"""

from __future__ import annotations

import hashlib
import json
import re

from .compressor import CompressedDOM

# A class/id token "looks hashed" (per-deploy CSS-module/Tailwind/styled-components noise)
# when it contains a digit AND a run of >=4 hex characters. Requiring a digit avoids
# normalizing real words made only of hex letters (e.g. "fade", "deadbeef" stays a word
# unless it carries a digit) while still catching "css-1a2b3c", "jss12ab", etc.
_HEX_RUN = re.compile(r"[0-9a-f]{4,}")
_TOKEN = re.compile(r"([.#])([A-Za-z0-9_-]+)")  # a .class or #id token in a descriptor
_HREF = re.compile(r'href="[^"]*"')
_HASHED = "·hashed"  # the placeholder a hashed token collapses to


def _looks_hashed(token: str) -> bool:
    t = token.lower()
    return any(ch.isdigit() for ch in t) and bool(_HEX_RUN.search(t))


def _normalize_tokens(text: str) -> str:
    """Collapse build-hashed class/id tokens to a stable placeholder so per-deploy hashes
    don't masquerade as structural drift."""
    return _TOKEN.sub(
        lambda m: m.group(1) + (_HASHED if _looks_hashed(m.group(2)) else m.group(2)),
        text,
    )


def _normalize_outline(outline: str) -> list[str]:
    """Strip everything content-bearing from the outline, keeping only the skeleton:
    drop text previews and repeat-count summary lines, turn `href="…"` into a bare flag,
    and collapse hashed class/id tokens. Indentation (depth) is preserved."""
    lines: list[str] = []
    for raw in outline.splitlines():
        if raw.lstrip().startswith("(+"):  # "(+N more `sig`)" — encodes a count
            continue
        line = raw.split(': "', 1)[0]  # drop the text preview
        line = _HREF.sub("[href]", line)
        line = _normalize_tokens(line)
        lines.append(line)
    return lines


def _collect_ld_types(node, out: set[str]) -> None:
    if isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, str):
            out.add(t)
        elif isinstance(t, list):
            out.update(x for x in t if isinstance(x, str))
        for value in node.values():
            _collect_ld_types(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_ld_types(item, out)


def _ld_types(blocks: list[str]) -> list[str]:
    """The sorted set of schema.org @type values present across all JSON-LD blocks —
    shape only, never the values (salaries/dates/titles)."""
    types: set[str] = set()
    for block in blocks:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            types.add("<unparsed-ld>")
            continue
        _collect_ld_types(data, types)
    return sorted(types)


def fingerprint(compressed: CompressedDOM) -> str:
    """A 16-char structural fingerprint of the compressed page.

    Reads the `CompressedDOM` fields directly (never `to_prompt()`, whose token-budget
    truncation must not affect the hash)."""
    payload = {
        "repeating": sorted(_normalize_tokens(g.signature) for g in compressed.repeating),
        "ld_types": _ld_types(compressed.json_ld_blocks),
        "outline": _normalize_outline(compressed.outline),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
