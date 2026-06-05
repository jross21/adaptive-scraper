"""The selector-map interpreter — the deterministic default execution path.

Walks an `ExtractionSpec` against a parsed page. No arbitrary code runs here (the
generated-code fallback + sandbox is a later phase), so this path is safe by
construction. Supports all four selector types: css, xpath, json_ld, regex.

Resolution model:
  - "many" + container_selector_type css/xpath: select repeating DOM elements; each
    FieldRule resolves *relative* to its element (json_ld/regex fields fall back to the
    page-level JSON-LD / the element's own HTML).
  - "many" + container_selector_type json_ld: container_selector names a schema.org
    @type; iterate matching JSON-LD objects; json_ld fields resolve by dotted path.
  - "one": resolve each FieldRule against the whole document; return a single row.

Failure policy: a *data* failure (e.g. int() over non-numeric text) is swallowed to
None so it surfaces as a high null-rate in validation. A *config* failure (an
unknown/un-whitelisted transform) raises — that's a bad spec, not bad data.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import parsel

from ..models.spec import ExtractionSpec, FieldRule
from .transforms import TransformContext, UnknownTransformError, apply_transform

Cardinality = Literal["one", "many"]


# --------------------------------------------------------------------------- #
# JSON-LD helpers
# --------------------------------------------------------------------------- #
def _flatten_jsonld(node: Any) -> list[dict]:
    """Expand arrays and @graph wrappers into a flat list of object dicts."""
    out: list[dict] = []
    if isinstance(node, list):
        for item in node:
            out.extend(_flatten_jsonld(item))
    elif isinstance(node, dict):
        graph = node.get("@graph")
        if isinstance(graph, list):
            out.extend(_flatten_jsonld(graph))
        out.append(node)
    return out


def _parse_jsonld(sel: parsel.Selector) -> list[dict]:
    objects: list[dict] = []
    for block in sel.css('script[type="application/ld+json"]::text').getall():
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        objects.extend(_flatten_jsonld(data))
    return objects


def _resolve_path(obj: dict, dotted: str) -> str | None:
    """Resolve a dotted path against a JSON-LD object, descending into the first
    element of any list encountered. Returns a string, or None if absent."""
    cur: Any = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    if isinstance(cur, list):
        cur = cur[0] if cur else None
    if isinstance(cur, (str, int, float)):
        return str(cur)
    return None


# --------------------------------------------------------------------------- #
# DOM resolution helpers
# --------------------------------------------------------------------------- #
def _text_of(sublist: parsel.SelectorList) -> str | None:
    if not sublist:
        return None
    return sublist.xpath("string(.)").get()


def _resolve_dom(node: parsel.Selector, rule: FieldRule) -> str | None:
    if rule.selector_type == "css":
        sub = node.css(rule.selector)
    else:  # xpath
        sub = node.xpath(rule.selector)
    if not sub:
        return None
    if rule.attribute and rule.attribute != "text":
        return sub.attrib.get(rule.attribute)
    return _text_of(sub)


def _resolve_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.DOTALL)
    if match is None:
        return None
    return match.group(1) if match.groups() else match.group(0)


# --------------------------------------------------------------------------- #
# Field resolution + transform
# --------------------------------------------------------------------------- #
def _raw_value(
    rule: FieldRule,
    node: parsel.Selector,
    node_html: str,
    page_jsonld: list[dict],
) -> str | None:
    if rule.selector_type in ("css", "xpath"):
        return _resolve_dom(node, rule)
    if rule.selector_type == "regex":
        return _resolve_regex(node_html, rule.selector)
    if rule.selector_type == "json_ld":
        for obj in page_jsonld:
            value = _resolve_path(obj, rule.selector)
            if value is not None:
                return value
        return None
    return None


def _apply(rule: FieldRule, raw: str | None, ctx: TransformContext) -> Any:
    if raw is None or rule.transform is None:
        return raw
    try:
        return apply_transform(rule.transform, raw, ctx)
    except UnknownTransformError:
        raise  # bad spec — fail loud
    except (ValueError, TypeError, OverflowError):
        return None  # bad data — tolerate, validation will flag the null-rate


def _row_from_dom(
    rules: list[FieldRule],
    node: parsel.Selector,
    page_jsonld: list[dict],
    ctx: TransformContext,
) -> dict[str, Any]:
    node_html = node.get() or ""
    row: dict[str, Any] = {}
    for rule in rules:
        raw = _raw_value(rule, node, node_html, page_jsonld)
        row[rule.field] = _apply(rule, raw, ctx)
    return row


def _row_from_jsonld_object(
    rules: list[FieldRule], obj: dict, ctx: TransformContext
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for rule in rules:
        raw = _resolve_path(obj, rule.selector) if rule.selector_type == "json_ld" else None
        row[rule.field] = _apply(rule, raw, ctx)
    return row


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def interpret(
    spec: ExtractionSpec,
    html: str,
    *,
    base_url: str | None,
    cardinality: Cardinality,
) -> list[dict[str, Any]]:
    sel = parsel.Selector(html)
    page_jsonld = _parse_jsonld(sel)
    ctx = TransformContext(base_url=base_url)

    if cardinality == "many" and spec.container_selector_type == "json_ld":
        wanted = spec.container_selector
        objects = [o for o in page_jsonld if _type_matches(o, wanted)]
        return [_row_from_jsonld_object(spec.field_rules, o, ctx) for o in objects]

    if cardinality == "many":
        container = spec.container_selector or "*"
        nodes = (
            sel.css(container)
            if spec.container_selector_type == "css"
            else sel.xpath(container)
        )
        return [_row_from_dom(spec.field_rules, n, page_jsonld, ctx) for n in nodes]

    # cardinality == "one": resolve against the whole document (the root node).
    return [_row_from_dom(spec.field_rules, sel, page_jsonld, ctx)]


def _type_matches(obj: dict, wanted: str | None) -> bool:
    if wanted is None:
        return True
    obj_type = obj.get("@type")
    if isinstance(obj_type, list):
        return wanted in obj_type
    return obj_type == wanted
