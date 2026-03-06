"""TASK-018: Layer 0 automated audit engine.

Stateless pure-function audit: evaluates every acceptance_criteria assertion
against the submitted result_data and returns a structured verdict.

Each assertion type maps to an evaluator function that returns (passed, detail).
All evaluators are intentionally side-effect-free so the engine can be
parallelised or run in Celery workers unchanged.
"""
from __future__ import annotations

import csv
import io
import json
import operator as op
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckResult:
    assertion_type: str
    passed: bool
    detail: str


@dataclass
class Layer0AuditResult:
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "type": c.assertion_type,
                    "passed": c.passed,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Operator helpers
# ---------------------------------------------------------------------------

_OPS: dict[str, Any] = {
    ">=": op.ge,
    "<=": op.le,
    ">": op.gt,
    "<": op.lt,
    "==": op.eq,
    "!=": op.ne,
}


def _apply_op(operator: str, lhs: Any, rhs: Any) -> bool:
    fn = _OPS.get(operator)
    if fn is None:
        return False
    try:
        return bool(fn(lhs, rhs))
    except TypeError:
        return False


# ---------------------------------------------------------------------------
# Per-assertion evaluators
# ---------------------------------------------------------------------------


def _eval_coverage_rate(assertion: dict, result: dict) -> CheckResult:
    threshold = assertion["threshold"]
    operator = assertion.get("operator", ">=")
    value = result.get("coverage_rate")
    if value is None:
        return CheckResult("coverage_rate", False, "field 'coverage_rate' not found in result")
    passed = _apply_op(operator, value, threshold)
    return CheckResult(
        "coverage_rate",
        passed,
        f"coverage_rate={value} {operator} {threshold}: {'ok' if passed else 'FAIL'}",
    )


def _eval_schema_compliance(assertion: dict, result: dict) -> CheckResult:
    schema = assertion["schema"]
    try:
        import jsonschema  # optional dep
        jsonschema.validate(result, schema)
        return CheckResult("schema_compliance", True, "JSON schema validation passed")
    except ImportError:
        return CheckResult("schema_compliance", True, "jsonschema not installed – skipped")
    except Exception as exc:
        return CheckResult("schema_compliance", False, f"schema validation failed: {exc}")


def _eval_field_completeness(assertion: dict, result: dict) -> CheckResult:
    required = assertion.get("required_fields", [])
    missing = [f for f in required if f not in result or result[f] is None]
    if missing:
        return CheckResult("field_completeness", False, f"missing or null fields: {missing}")
    return CheckResult("field_completeness", True, "all required fields present")


def _eval_no_hallucinated_fields(assertion: dict, result: dict) -> CheckResult:
    allowed = set(assertion.get("allowed_fields", []))
    extra = [f for f in result if f not in allowed]
    if extra:
        return CheckResult(
            "no_hallucinated_fields", False, f"unexpected fields in result: {extra}"
        )
    return CheckResult("no_hallucinated_fields", True, "no unexpected fields")


def _eval_value_range(assertion: dict, result: dict) -> CheckResult:
    fname = assertion["field"]
    min_v = assertion["min"]
    max_v = assertion["max"]
    value = result.get(fname)
    if value is None:
        return CheckResult("value_range", False, f"field '{fname}' not found")
    try:
        num = float(value)
    except (TypeError, ValueError):
        return CheckResult("value_range", False, f"field '{fname}' is not numeric: {value!r}")
    passed = min_v <= num <= max_v
    return CheckResult(
        "value_range",
        passed,
        f"{fname}={num} in [{min_v}, {max_v}]: {'ok' if passed else 'FAIL'}",
    )


def _eval_regex_match(assertion: dict, result: dict) -> CheckResult:
    fname = assertion["field"]
    pattern = assertion["pattern"]
    value = result.get(fname)
    if value is None:
        return CheckResult("regex_match", False, f"field '{fname}' not found")
    matched = bool(re.search(pattern, str(value)))
    return CheckResult(
        "regex_match",
        matched,
        f"field '{fname}' {'matches' if matched else 'does not match'} pattern",
    )


def _eval_honeypot_exact_match(
    assertion: dict, result: dict, honeypot_answers: dict | None
) -> CheckResult:
    fname = assertion["field"]
    if not honeypot_answers or fname not in honeypot_answers:
        # No honeypot data seeded for this field – gracefully skip
        return CheckResult("honeypot_exact_match", True, f"no honeypot answer stored for '{fname}' – skipped")
    expected = honeypot_answers[fname]
    actual = result.get(fname)
    passed = actual == expected
    return CheckResult(
        "honeypot_exact_match",
        passed,
        f"honeypot field '{fname}': {'matched' if passed else 'MISMATCH'}",
    )


def _eval_row_count(assertion: dict, result: dict) -> CheckResult:
    operator = assertion["operator"]
    threshold = assertion["value"]
    # Accept list result or dict with "rows" key
    if isinstance(result, list):
        count = len(result)
    elif isinstance(result, dict) and "rows" in result:
        count = len(result["rows"])
    elif isinstance(result, dict) and "data" in result:
        count = len(result["data"])
    else:
        return CheckResult("row_count", False, "result must be a list or contain 'rows'/'data' key")
    passed = _apply_op(operator, count, threshold)
    return CheckResult(
        "row_count",
        passed,
        f"row_count={count} {operator} {threshold}: {'ok' if passed else 'FAIL'}",
    )


def _eval_json_valid(assertion: dict, result: dict) -> CheckResult:
    # result_data is already parsed as a dict – always valid JSON
    field_name = assertion.get("field")
    if field_name:
        value = result.get(field_name)
        if value is None:
            return CheckResult("json_valid", False, f"field '{field_name}' not found")
        try:
            if isinstance(value, str):
                json.loads(value)
            # if already dict/list it's valid
        except json.JSONDecodeError as exc:
            return CheckResult("json_valid", False, f"field '{field_name}' is not valid JSON: {exc}")
    return CheckResult("json_valid", True, "JSON structure valid")


def _eval_csv_valid(assertion: dict, result: dict) -> CheckResult:
    field_name = assertion.get("field", "csv_content")
    value = result.get(field_name)
    if value is None:
        return CheckResult("csv_valid", False, f"field '{field_name}' not found in result")
    if not isinstance(value, str):
        return CheckResult("csv_valid", False, f"field '{field_name}' must be a string")
    try:
        reader = csv.reader(io.StringIO(value))
        rows = list(reader)
        if len(rows) < 2:  # at least header + 1 data row
            return CheckResult("csv_valid", False, "CSV must have at least one data row")
        return CheckResult("csv_valid", True, f"valid CSV with {len(rows) - 1} data rows")
    except csv.Error as exc:
        return CheckResult("csv_valid", False, f"CSV parse error: {exc}")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_EVALUATORS = {
    "coverage_rate": _eval_coverage_rate,
    "schema_compliance": _eval_schema_compliance,
    "field_completeness": _eval_field_completeness,
    "no_hallucinated_fields": _eval_no_hallucinated_fields,
    "value_range": _eval_value_range,
    "regex_match": _eval_regex_match,
    "honeypot_exact_match": _eval_honeypot_exact_match,
    "row_count": _eval_row_count,
    "json_valid": _eval_json_valid,
    "csv_valid": _eval_csv_valid,
}


def run_layer0_audit(
    acceptance_criteria: list[dict],
    result_data: dict,
    honeypot_answers: dict | None = None,
) -> Layer0AuditResult:
    """Evaluate all assertions in *acceptance_criteria* against *result_data*.

    Returns a Layer0AuditResult; overall passed = all assertions passed.
    Unknown assertion types are treated as warnings (skipped, not fail).
    """
    checks: list[CheckResult] = []

    for assertion in acceptance_criteria:
        atype = assertion.get("type", "")
        evaluator = _EVALUATORS.get(atype)

        if evaluator is None:
            checks.append(CheckResult(atype, True, f"unknown type '{atype}' – skipped"))
            continue

        if atype == "honeypot_exact_match":
            check = _eval_honeypot_exact_match(assertion, result_data, honeypot_answers)
        else:
            try:
                check = evaluator(assertion, result_data)
            except Exception as exc:
                check = CheckResult(atype, False, f"evaluator error: {exc}")

        checks.append(check)

    overall_passed = all(c.passed for c in checks)
    return Layer0AuditResult(passed=overall_passed, checks=checks)
