"""TASK-018/019 tests: Layer 0 audit engine unit tests.

Covers the 10 assertion types (pass and fail paths) plus honeypot mechanism.
"""
import pytest

from app.services.layer0_audit import run_layer0_audit


def _run(criteria, result, honeypot=None):
    return run_layer0_audit(criteria, result, honeypot_answers=honeypot)


# ---------------------------------------------------------------------------
# coverage_rate
# ---------------------------------------------------------------------------


def test_coverage_rate_pass():
    r = _run([{"type": "coverage_rate", "threshold": 0.90}], {"coverage_rate": 0.95})
    assert r.passed


def test_coverage_rate_fail():
    r = _run([{"type": "coverage_rate", "threshold": 0.90}], {"coverage_rate": 0.80})
    assert not r.passed


def test_coverage_rate_missing_field():
    r = _run([{"type": "coverage_rate", "threshold": 0.90}], {"other": 1})
    assert not r.passed


# ---------------------------------------------------------------------------
# field_completeness
# ---------------------------------------------------------------------------


def test_field_completeness_pass():
    r = _run(
        [{"type": "field_completeness", "required_fields": ["id", "name"]}],
        {"id": 1, "name": "Alice"},
    )
    assert r.passed


def test_field_completeness_fail():
    r = _run(
        [{"type": "field_completeness", "required_fields": ["id", "name"]}],
        {"id": 1},
    )
    assert not r.passed
    assert any("name" in c.detail for c in r.checks)


def test_field_completeness_null_counts_as_missing():
    r = _run(
        [{"type": "field_completeness", "required_fields": ["value"]}],
        {"value": None},
    )
    assert not r.passed


# ---------------------------------------------------------------------------
# no_hallucinated_fields
# ---------------------------------------------------------------------------


def test_no_hallucinated_fields_pass():
    r = _run(
        [{"type": "no_hallucinated_fields", "allowed_fields": ["id", "name"]}],
        {"id": 1, "name": "Alice"},
    )
    assert r.passed


def test_no_hallucinated_fields_fail():
    r = _run(
        [{"type": "no_hallucinated_fields", "allowed_fields": ["id"]}],
        {"id": 1, "extra": "unauthorized"},
    )
    assert not r.passed


# ---------------------------------------------------------------------------
# value_range
# ---------------------------------------------------------------------------


def test_value_range_pass():
    r = _run(
        [{"type": "value_range", "field": "score", "min": 0, "max": 100}],
        {"score": 75},
    )
    assert r.passed


def test_value_range_fail_above_max():
    r = _run(
        [{"type": "value_range", "field": "score", "min": 0, "max": 100}],
        {"score": 150},
    )
    assert not r.passed


# ---------------------------------------------------------------------------
# regex_match
# ---------------------------------------------------------------------------


def test_regex_match_pass():
    r = _run(
        [{"type": "regex_match", "field": "email", "pattern": r"@example\.com$"}],
        {"email": "alice@example.com"},
    )
    assert r.passed


def test_regex_match_fail():
    r = _run(
        [{"type": "regex_match", "field": "email", "pattern": r"@example\.com$"}],
        {"email": "alice@other.com"},
    )
    assert not r.passed


# ---------------------------------------------------------------------------
# honeypot_exact_match  (TASK-020)
# ---------------------------------------------------------------------------


def test_honeypot_pass():
    r = _run(
        [{"type": "honeypot_exact_match", "field": "answer"}],
        {"answer": 42},
        honeypot={"answer": 42},
    )
    assert r.passed


def test_honeypot_fail():
    r = _run(
        [{"type": "honeypot_exact_match", "field": "answer"}],
        {"answer": 99},
        honeypot={"answer": 42},
    )
    assert not r.passed


def test_honeypot_no_data_skips():
    r = _run(
        [{"type": "honeypot_exact_match", "field": "answer"}],
        {"answer": 99},
        honeypot=None,
    )
    assert r.passed  # graceful skip when no honeypot configured


# ---------------------------------------------------------------------------
# row_count
# ---------------------------------------------------------------------------


def test_row_count_pass_list():
    r = _run(
        [{"type": "row_count", "operator": ">=", "value": 3}],
        [{"a": 1}, {"a": 2}, {"a": 3}],
    )
    assert r.passed


def test_row_count_fail():
    r = _run(
        [{"type": "row_count", "operator": ">=", "value": 5}],
        [{"a": 1}],
    )
    assert not r.passed


def test_row_count_dict_with_rows_key():
    r = _run(
        [{"type": "row_count", "operator": "==", "value": 2}],
        {"rows": [{"a": 1}, {"a": 2}]},
    )
    assert r.passed


# ---------------------------------------------------------------------------
# json_valid / csv_valid
# ---------------------------------------------------------------------------


def test_json_valid_dict_result():
    r = _run([{"type": "json_valid"}], {"key": "value"})
    assert r.passed


def test_csv_valid_pass():
    r = _run(
        [{"type": "csv_valid"}],
        {"csv_content": "name,age\nAlice,30\nBob,25"},
    )
    assert r.passed


def test_csv_valid_fail_no_data_row():
    r = _run(
        [{"type": "csv_valid"}],
        {"csv_content": "name,age"},
    )
    assert not r.passed


# ---------------------------------------------------------------------------
# Multi-assertion: overall pass requires ALL to pass
# ---------------------------------------------------------------------------


def test_all_assertions_must_pass():
    r = _run(
        [
            {"type": "field_completeness", "required_fields": ["id", "name"]},
            {"type": "coverage_rate", "threshold": 0.95},
        ],
        {"id": 1, "name": "Alice", "coverage_rate": 0.80},  # coverage fails
    )
    assert not r.passed
    assert len(r.checks) == 2
    passed_checks = [c for c in r.checks if c.passed]
    assert len(passed_checks) == 1  # field_completeness passes, coverage_rate fails
