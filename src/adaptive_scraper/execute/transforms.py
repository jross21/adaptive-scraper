"""Whitelisted transform registry.

A `FieldRule.transform` names one of these. This is deliberately a closed registry
of named functions, not free-text code — it keeps execution deterministic and safe
(no arbitrary code runs in the default selector-map path). The codegen prompt lists
exactly these names; anything else is rejected.

Numeric/date transforms raise on unparseable input; the interpreter catches that and
records the field as missing, so a bad mapping shows up as a high null-rate in
validation rather than crashing the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin

from dateutil import parser as date_parser


class UnknownTransformError(ValueError):
    """Raised when a FieldRule names a transform that isn't whitelisted."""


@dataclass(frozen=True)
class TransformContext:
    """Side data a transform may need (e.g. the page URL for resolving links)."""

    base_url: str | None = None


def _trim(value: str, ctx: TransformContext) -> str:
    return value.strip()


def _collapse_ws(value: str, ctx: TransformContext) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _lower(value: str, ctx: TransformContext) -> str:
    return value.lower()


def _upper(value: str, ctx: TransformContext) -> str:
    return value.upper()


_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _int(value: str, ctx: TransformContext) -> int:
    match = _NUM_RE.search(value)
    if match is None:
        raise ValueError(f"no integer found in {value!r}")
    return int(match.group(0).replace(",", "").split(".")[0])


def _float(value: str, ctx: TransformContext) -> float:
    match = _NUM_RE.search(value)
    if match is None:
        raise ValueError(f"no number found in {value!r}")
    return float(match.group(0).replace(",", ""))


def _iso_date(value: str, ctx: TransformContext) -> str:
    return date_parser.parse(value).isoformat()


def _abs_url(value: str, ctx: TransformContext) -> str:
    if not ctx.base_url:
        return value
    return urljoin(ctx.base_url, value)


_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off", ""}


def _bool(value: str, ctx: TransformContext) -> bool:
    token = value.strip().lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    raise ValueError(f"cannot interpret {value!r} as a boolean")


TRANSFORMS: dict[str, Callable[[str, TransformContext], Any]] = {
    "trim": _trim,
    "collapse_ws": _collapse_ws,
    "lower": _lower,
    "upper": _upper,
    "int": _int,
    "float": _float,
    "iso_date": _iso_date,
    "abs_url": _abs_url,
    "bool": _bool,
}

#: Names exposed to the codegen agent so it only ever proposes whitelisted transforms.
TRANSFORM_NAMES: tuple[str, ...] = tuple(TRANSFORMS)


def apply_transform(name: str, value: str | None, ctx: TransformContext) -> Any:
    """Apply a named transform to a value. None passes through untransformed."""
    if value is None:
        return None
    fn = TRANSFORMS.get(name)
    if fn is None:
        raise UnknownTransformError(
            f"unknown transform {name!r}; allowed: {', '.join(TRANSFORM_NAMES)}"
        )
    return fn(value, ctx)
