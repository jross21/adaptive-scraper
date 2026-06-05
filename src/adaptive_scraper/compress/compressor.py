"""DOM compression — the biggest leverage point.

The model never sees raw HTML. It reasons over a distilled view: surfaced JSON-LD
(the cheat code), exemplars of any repeating structures (a few + a count, not all N),
and a compact tag-tree outline. Noise (`<script>` except JSON-LD, `<style>`, `<svg>`,
comments, most attributes) is stripped and long text is truncated, all under a token
budget.

Token budget is enforced as a hard character cap on the rendered prompt, with content
assembled in priority order (JSON-LD -> exemplars -> outline) so the highest-signal
material survives truncation.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

from pydantic import BaseModel
from selectolax.parser import HTMLParser, Node

# Attributes worth keeping; everything else is dropped to save tokens.
_KEEP_ATTRS = ("id", "class", "href", "src")
_DROP_TAGS = ("style", "svg", "noscript", "iframe", "template")
_TEXT_PREVIEW = 80
_MIN_REPEAT = 3
_MAX_EXEMPLARS = 2
_MAX_DEPTH = 8
_DEFAULT_TOKEN_BUDGET = 10_000
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class RepeatingGroup(BaseModel):
    signature: str  # e.g. "li.job-card"
    count: int
    exemplars: list[str]  # cleaned outer HTML of up to _MAX_EXEMPLARS items


class CompressedDOM(BaseModel):
    title: str | None
    json_ld_blocks: list[str]
    repeating: list[RepeatingGroup]
    outline: str

    def to_prompt(self, token_budget: int = _DEFAULT_TOKEN_BUDGET) -> str:
        parts: list[str] = []
        if self.title:
            parts.append(f"# PAGE TITLE\n{self.title}")
        if self.json_ld_blocks:
            parts.append("# JSON-LD (structured data found on the page)\n" + "\n\n".join(self.json_ld_blocks))
        if self.repeating:
            chunks = []
            for g in self.repeating:
                ex = "\n".join(g.exemplars)
                chunks.append(f"## repeating `{g.signature}` x{g.count} (exemplars)\n{ex}")
            parts.append("# REPEATING STRUCTURES\n" + "\n\n".join(chunks))
        parts.append("# DOM OUTLINE\n" + self.outline)

        prompt = "\n\n".join(parts)
        cap = token_budget * 4  # ~4 chars per token
        if len(prompt) > cap:
            prompt = prompt[:cap]
        return prompt


# --------------------------------------------------------------------------- #
def _signature(node: Node) -> str:
    classes = (node.attributes.get("class") or "").split()
    return node.tag + "".join(f".{c}" for c in classes)


def _element_children(node: Node) -> list[Node]:
    return [c for c in node.iter(include_text=False)]


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_json_ld(tree: HTMLParser) -> list[str]:
    blocks: list[str] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text(deep=True) or ""
        try:
            blocks.append(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
        except (json.JSONDecodeError, ValueError):
            blocks.append(raw.strip())
    return blocks


def _strip_noise(tree: HTMLParser) -> None:
    for node in tree.css(",".join(_DROP_TAGS)):
        node.decompose()
    for node in tree.css("script"):  # keep nothing in the visual tree; JSON-LD pulled separately
        node.decompose()


def _detect_repeating(tree: HTMLParser) -> list[RepeatingGroup]:
    groups: list[RepeatingGroup] = []
    seen: set[str] = set()
    root = tree.css_first("body") or tree.root
    if root is None:
        return groups
    for parent in _iter_elements(root):
        buckets: dict[str, list[Node]] = {}
        for child in _element_children(parent):
            buckets.setdefault(_signature(child), []).append(child)
        for sig, nodes in buckets.items():
            if len(nodes) >= _MIN_REPEAT and sig not in seen and _has_signal(nodes[0]):
                seen.add(sig)
                groups.append(
                    RepeatingGroup(
                        signature=sig,
                        count=len(nodes),
                        exemplars=[_clean_html(n) for n in nodes[:_MAX_EXEMPLARS]],
                    )
                )
    groups.sort(key=lambda g: g.count, reverse=True)
    return groups


def _has_signal(node: Node) -> bool:
    return bool((node.text(deep=True) or "").strip())


def _iter_elements(node: Node) -> Iterable[Node]:
    yield node
    for child in node.iter(include_text=False):
        yield from _iter_elements(child)


def _clean_html(node: Node) -> str:
    """Serialize a node keeping only useful attributes and collapsed text."""
    descriptor = _descriptor(node)
    text = _collapse_ws(node.text(deep=True) or "")
    if len(text) > 200:
        text = text[:200] + "…"
    return f"<{descriptor}> {text}"


def _descriptor(node: Node) -> str:
    attrs = node.attributes
    parts = [node.tag]
    if attrs.get("id"):
        parts.append(f'#{attrs["id"]}')
    classes = (attrs.get("class") or "").split()
    parts.extend(f".{c}" for c in classes)
    href = attrs.get("href")
    if href:
        parts.append(f'href="{href}"')
    return " ".join(parts) if len(parts) > 1 else parts[0]


def _outline(tree: HTMLParser) -> str:
    root = tree.css_first("body") or tree.root
    if root is None:
        return ""
    lines: list[str] = []
    _outline_node(root, 0, lines)
    return "\n".join(lines)


def _outline_node(node: Node, depth: int, lines: list[str]) -> None:
    if depth > _MAX_DEPTH:
        return
    indent = "  " * depth
    own_text = _collapse_ws(_own_text(node))
    line = f"{indent}{_descriptor(node)}"
    if own_text:
        preview = own_text[:_TEXT_PREVIEW] + ("…" if len(own_text) > _TEXT_PREVIEW else "")
        line += f': "{preview}"'
    lines.append(line)

    children = _element_children(node)
    i = 0
    while i < len(children):
        child = children[i]
        sig = _signature(child)
        run = 1
        while i + run < len(children) and _signature(children[i + run]) == sig:
            run += 1
        # Render the first of a repeated run, then summarize the remainder.
        _outline_node(child, depth + 1, lines)
        if run >= _MIN_REPEAT:
            lines.append(f"{'  ' * (depth + 1)}(+{run - 1} more `{sig}`)")
            i += run
        else:
            i += 1


def _own_text(node: Node) -> str:
    """Direct text of this node, excluding descendants' text."""
    chunks = []
    for child in node.iter(include_text=True):
        if child.tag == "-text":
            chunks.append(child.text(deep=False) or "")
    return "".join(chunks)


# --------------------------------------------------------------------------- #
def compress(html: str, *, token_budget: int = _DEFAULT_TOKEN_BUDGET) -> CompressedDOM:
    json_ld_blocks = _extract_json_ld(HTMLParser(html))  # pull before stripping scripts

    cleaned_html = _COMMENT_RE.sub("", html)
    tree = HTMLParser(cleaned_html)
    title_node = tree.css_first("title")
    title = _collapse_ws(title_node.text(deep=True)) if title_node else None

    _strip_noise(tree)
    repeating = _detect_repeating(tree)
    outline = _outline(tree)

    return CompressedDOM(
        title=title,
        json_ld_blocks=json_ld_blocks,
        repeating=repeating,
        outline=outline,
    )
