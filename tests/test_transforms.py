import pytest

from adaptive_scraper.execute.transforms import (
    TransformContext,
    UnknownTransformError,
    apply_transform,
)

CTX = TransformContext(base_url="https://jobs.example.com/list")


def test_trim():
    assert apply_transform("trim", "  hi  ", CTX) == "hi"


def test_collapse_ws():
    assert apply_transform("collapse_ws", "a\n  b\t c", CTX) == "a b c"


def test_lower_and_upper():
    assert apply_transform("lower", "AbC", CTX) == "abc"
    assert apply_transform("upper", "AbC", CTX) == "ABC"


def test_int_strips_surrounding_punctuation():
    assert apply_transform("int", "$1,234", CTX) == 1234


def test_int_raises_on_no_digits():
    with pytest.raises(ValueError):
        apply_transform("int", "n/a", CTX)


def test_float_parses_currency():
    assert apply_transform("float", "$1.50", CTX) == 1.5


def test_iso_date_parses_human_date():
    assert apply_transform("iso_date", "Jan 15, 2024", CTX).startswith("2024-01-15")


def test_abs_url_resolves_relative():
    assert apply_transform("abs_url", "/jobs/42", CTX) == "https://jobs.example.com/jobs/42"


def test_abs_url_passthrough_when_no_base():
    ctx = TransformContext(base_url=None)
    assert apply_transform("abs_url", "https://x/y", ctx) == "https://x/y"


def test_bool_truthy_and_falsy():
    assert apply_transform("bool", "Yes", CTX) is True
    assert apply_transform("bool", "0", CTX) is False


def test_none_value_passes_through_untransformed():
    assert apply_transform("trim", None, CTX) is None


def test_unknown_transform_is_rejected():
    # Whitelist-only: an unknown name must never execute anything.
    with pytest.raises(UnknownTransformError):
        apply_transform("eval_code", "x", CTX)
