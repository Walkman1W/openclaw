"""TASK-009 tests: acceptance_criteria validator.

Covers 10 valid formats and 5 invalid formats per TASKS.md acceptance criteria.
"""
import pytest

from app.services.criteria_validator import CriteriaValidationError, validate_acceptance_criteria


# ---------------------------------------------------------------------------
# 10 valid formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "criteria",
    [
        # 1. coverage_rate with default operator
        [{"type": "coverage_rate", "threshold": 0.95}],
        # 2. coverage_rate with explicit operator
        [{"type": "coverage_rate", "threshold": 0.80, "operator": ">="}],
        # 3. schema_compliance
        [{"type": "schema_compliance", "schema": {"type": "object", "properties": {}}}],
        # 4. field_completeness
        [{"type": "field_completeness", "required_fields": ["name", "age"]}],
        # 5. no_hallucinated_fields
        [{"type": "no_hallucinated_fields", "allowed_fields": ["id", "value"]}],
        # 6. value_range
        [{"type": "value_range", "field": "score", "min": 0, "max": 100}],
        # 7. regex_match
        [{"type": "regex_match", "field": "email", "pattern": r"^[\w.]+@[\w.]+$"}],
        # 8. honeypot_exact_match
        [{"type": "honeypot_exact_match", "field": "answer"}],
        # 9. row_count
        [{"type": "row_count", "operator": ">=", "value": 10}],
        # 10. multiple assertions combined
        [
            {"type": "coverage_rate", "threshold": 0.90},
            {"type": "field_completeness", "required_fields": ["id"]},
            {"type": "json_valid"},
        ],
    ],
)
def test_valid_criteria(criteria):
    validate_acceptance_criteria(criteria)  # must not raise


# ---------------------------------------------------------------------------
# 5 invalid formats
# ---------------------------------------------------------------------------


def test_invalid_not_a_list():
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria({"type": "coverage_rate", "threshold": 0.9})
    assert any("JSON array" in e for e in exc_info.value.errors)


def test_invalid_empty_list():
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria([])
    assert any("at least one" in e for e in exc_info.value.errors)


def test_invalid_unknown_type():
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria([{"type": "output_quality_good"}])
    assert any("output_quality_good" in e for e in exc_info.value.errors)


def test_invalid_missing_required_param():
    # coverage_rate requires 'threshold'
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria([{"type": "coverage_rate"}])
    assert any("threshold" in e for e in exc_info.value.errors)


def test_invalid_threshold_out_of_range():
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria([{"type": "coverage_rate", "threshold": 1.5}])
    assert any("0.0 and 1.0" in e for e in exc_info.value.errors)


# ---------------------------------------------------------------------------
# Additional edge-case checks
# ---------------------------------------------------------------------------


def test_invalid_value_range_min_gt_max():
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria(
            [{"type": "value_range", "field": "score", "min": 100, "max": 0}]
        )
    assert any("min" in e and "max" in e for e in exc_info.value.errors)


def test_invalid_bad_regex():
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria(
            [{"type": "regex_match", "field": "x", "pattern": "[invalid"}]
        )
    assert any("regex" in e for e in exc_info.value.errors)


def test_invalid_assertion_not_object():
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria(["subjective description of quality"])
    assert any("JSON object" in e for e in exc_info.value.errors)


def test_collects_all_errors_in_one_pass():
    """Multiple invalid assertions should yield all errors at once."""
    criteria = [
        {"type": "unknown_type_a"},
        {"type": "unknown_type_b"},
    ]
    with pytest.raises(CriteriaValidationError) as exc_info:
        validate_acceptance_criteria(criteria)
    assert len(exc_info.value.errors) >= 2


def test_csv_valid_and_json_valid_need_no_params():
    validate_acceptance_criteria([{"type": "csv_valid"}, {"type": "json_valid"}])
