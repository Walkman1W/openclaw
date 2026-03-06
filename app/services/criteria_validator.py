"""TASK-009: acceptance_criteria legality validator.

acceptance_criteria must be a non-empty list of assertion objects.
Each assertion must have a 'type' field from the whitelist, plus
the required parameters for that type.

Raises CriteriaValidationError (a ValueError subclass) with a list of
all violations found in a single pass.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Whitelist: assertion_type -> {required_param: expected Python type(s)}
# ---------------------------------------------------------------------------
_WHITELIST: dict[str, dict[str, type | tuple[type, ...]]] = {
    # Fraction of records / tokens that pass; threshold in [0.0, 1.0]
    "coverage_rate": {"threshold": (int, float)},
    # Output JSON must conform to a given JSON Schema
    "schema_compliance": {"schema": dict},
    # All listed fields must be present and non-null
    "field_completeness": {"required_fields": list},
    # No fields outside the allow-list may appear
    "no_hallucinated_fields": {"allowed_fields": list},
    # A specific numeric field must fall within [min, max]
    "value_range": {"field": str, "min": (int, float), "max": (int, float)},
    # A specific string field must match a regex
    "regex_match": {"field": str, "pattern": str},
    # A honeypot field must exactly equal a pre-seeded expected value
    "honeypot_exact_match": {"field": str},
    # The output must have at least / at most N rows
    "row_count": {"operator": str, "value": int},
    # The output (or a named field) must be valid JSON
    "json_valid": {},
    # The output must be parseable as CSV with at least one data row
    "csv_valid": {},
}

_VALID_OPERATORS = {">=", "<=", ">", "<", "==", "!="}


class CriteriaValidationError(ValueError):
    """Raised when acceptance_criteria fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_acceptance_criteria(criteria: Any) -> None:
    """Validate *criteria* is a legal acceptance_criteria value.

    Collects all violations in a single pass and raises
    CriteriaValidationError if any are found.
    """
    errors: list[str] = []

    if not isinstance(criteria, list):
        raise CriteriaValidationError(
            ["acceptance_criteria must be a JSON array of assertion objects"]
        )

    if len(criteria) == 0:
        raise CriteriaValidationError(
            ["acceptance_criteria must contain at least one assertion"]
        )

    for idx, assertion in enumerate(criteria):
        prefix = f"assertion[{idx}]"

        if not isinstance(assertion, dict):
            errors.append(f"{prefix}: each item must be a JSON object, got {type(assertion).__name__!r}")
            continue

        type_val = assertion.get("type")
        if type_val is None:
            errors.append(f"{prefix}: missing required field 'type'")
            continue

        if not isinstance(type_val, str):
            errors.append(f"{prefix}.type: must be a string, got {type(type_val).__name__!r}")
            continue

        if type_val not in _WHITELIST:
            errors.append(
                f"{prefix}.type: '{type_val}' is not a recognised assertion type. "
                f"Allowed: {sorted(_WHITELIST)}"
            )
            continue

        # Check required parameters exist and have correct types
        required_params = _WHITELIST[type_val]
        for param, expected in required_params.items():
            if param not in assertion:
                errors.append(
                    f"{prefix}: missing required field '{param}' for type '{type_val}'"
                )
            elif not isinstance(assertion[param], expected):
                type_names = (
                    " or ".join(t.__name__ for t in expected)
                    if isinstance(expected, tuple)
                    else expected.__name__
                )
                errors.append(
                    f"{prefix}.{param}: expected {type_names}, "
                    f"got {type(assertion[param]).__name__!r}"
                )

        # Per-type semantic checks
        if type_val == "coverage_rate":
            threshold = assertion.get("threshold")
            if isinstance(threshold, (int, float)) and not (0.0 <= threshold <= 1.0):
                errors.append(
                    f"{prefix}.threshold: must be between 0.0 and 1.0, got {threshold}"
                )
            operator = assertion.get("operator", ">=")
            if operator not in _VALID_OPERATORS:
                errors.append(
                    f"{prefix}.operator: '{operator}' is not valid; use one of {sorted(_VALID_OPERATORS)}"
                )

        elif type_val == "value_range":
            min_v = assertion.get("min")
            max_v = assertion.get("max")
            if (
                isinstance(min_v, (int, float))
                and isinstance(max_v, (int, float))
                and min_v > max_v
            ):
                errors.append(
                    f"{prefix}: 'min' ({min_v}) must not exceed 'max' ({max_v})"
                )

        elif type_val == "row_count":
            operator = assertion.get("operator")
            if isinstance(operator, str) and operator not in _VALID_OPERATORS:
                errors.append(
                    f"{prefix}.operator: '{operator}' is not valid; use one of {sorted(_VALID_OPERATORS)}"
                )
            value = assertion.get("value")
            if isinstance(value, int) and value < 0:
                errors.append(f"{prefix}.value: row count must be >= 0, got {value}")

        elif type_val == "regex_match":
            pattern = assertion.get("pattern")
            if isinstance(pattern, str):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"{prefix}.pattern: invalid regex — {exc}")

        elif type_val == "field_completeness":
            fields = assertion.get("required_fields", [])
            if isinstance(fields, list) and len(fields) == 0:
                errors.append(f"{prefix}.required_fields: must contain at least one field name")

        elif type_val == "no_hallucinated_fields":
            fields = assertion.get("allowed_fields", [])
            if isinstance(fields, list) and len(fields) == 0:
                errors.append(f"{prefix}.allowed_fields: must contain at least one field name")

    if errors:
        raise CriteriaValidationError(errors)
