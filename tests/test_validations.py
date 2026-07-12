"""Unit tests for src/naru/validations.py."""

import pandas as pd

from naru.artifact import (
    KeyUniqueness,
    NullPolicy,
    RowCountBounds,
    SumPreservation,
    Validations,
    ValueRange,
)
from naru.validations import ValidationOutcome, run_validations


def _outcomes_by_name(
    outcomes: list[ValidationOutcome], prefix: str | None = None
) -> dict[str, ValidationOutcome]:
    if prefix is None:
        return {o.check_name: o for o in outcomes}
    return {o.check_name: o for o in outcomes if o.check_name.startswith(prefix)}


class TestRowCount:
    def test_within_bounds_passes(self) -> None:
        validations = Validations(row_count=RowCountBounds(min=1, max=5))
        outcomes = run_validations(validations, pd.DataFrame(), pd.DataFrame({"x": [1, 2]}))
        assert _outcomes_by_name(outcomes)["row_count"].status == "PASS"

    def test_below_min_fails(self) -> None:
        validations = Validations(row_count=RowCountBounds(min=5))
        outcomes = run_validations(validations, pd.DataFrame(), pd.DataFrame({"x": [1, 2]}))
        outcome = _outcomes_by_name(outcomes)["row_count"]
        assert outcome.status == "FAIL"
        assert outcome.detail is not None
        assert "minimum 5" in outcome.detail

    def test_above_max_fails(self) -> None:
        validations = Validations(row_count=RowCountBounds(max=1))
        outcomes = run_validations(validations, pd.DataFrame(), pd.DataFrame({"x": [1, 2]}))
        outcome = _outcomes_by_name(outcomes)["row_count"]
        assert outcome.status == "FAIL"
        assert outcome.detail is not None
        assert "maximum 1" in outcome.detail

    def test_no_bounds_always_passes(self) -> None:
        validations = Validations(row_count=RowCountBounds())
        outcomes = run_validations(validations, pd.DataFrame(), pd.DataFrame({"x": [1, 2]}))
        assert _outcomes_by_name(outcomes)["row_count"].status == "PASS"


class TestKeyUniqueness:
    def test_unique_keys_pass(self) -> None:
        validations = Validations(key_uniqueness=[KeyUniqueness(columns=["id"])])
        df = pd.DataFrame({"id": [1, 2, 3]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        assert _outcomes_by_name(outcomes)["key_uniqueness:id"].status == "PASS"

    def test_duplicate_keys_fail_naming_the_dupes(self) -> None:
        validations = Validations(key_uniqueness=[KeyUniqueness(columns=["id"])])
        df = pd.DataFrame({"id": [1, 1, 2]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        outcome = _outcomes_by_name(outcomes)["key_uniqueness:id"]
        assert outcome.status == "FAIL"
        assert outcome.detail is not None
        assert "1" in outcome.detail

    def test_composite_key(self) -> None:
        validations = Validations(key_uniqueness=[KeyUniqueness(columns=["a", "b"])])
        df = pd.DataFrame({"a": [1, 1], "b": [1, 2]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        assert _outcomes_by_name(outcomes)["key_uniqueness:a,b"].status == "PASS"


class TestNullPolicy:
    def test_no_nulls_passes_when_disallowed(self) -> None:
        validations = Validations(null_policy=[NullPolicy(column="x", nulls_allowed=False)])
        df = pd.DataFrame({"x": [1, 2]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        assert _outcomes_by_name(outcomes)["null_policy:x"].status == "PASS"

    def test_nulls_present_fails_when_disallowed(self) -> None:
        validations = Validations(null_policy=[NullPolicy(column="x", nulls_allowed=False)])
        df = pd.DataFrame({"x": [1, None]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        outcome = _outcomes_by_name(outcomes)["null_policy:x"]
        assert outcome.status == "FAIL"
        assert outcome.detail is not None
        assert "1 null" in outcome.detail

    def test_nulls_present_passes_when_allowed(self) -> None:
        validations = Validations(null_policy=[NullPolicy(column="x", nulls_allowed=True)])
        df = pd.DataFrame({"x": [1, None]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        assert _outcomes_by_name(outcomes)["null_policy:x"].status == "PASS"


class TestValueRanges:
    def test_within_range_passes(self) -> None:
        validations = Validations(value_ranges=[ValueRange(column="x", min=0.0, max=10.0)])
        df = pd.DataFrame({"x": [1.0, 5.0]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        assert _outcomes_by_name(outcomes)["value_range:x"].status == "PASS"

    def test_below_min_fails_naming_values(self) -> None:
        validations = Validations(value_ranges=[ValueRange(column="x", min=0.0)])
        df = pd.DataFrame({"x": [1.0, -5.0]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        outcome = _outcomes_by_name(outcomes)["value_range:x"]
        assert outcome.status == "FAIL"
        assert outcome.detail is not None
        assert "-5.0" in outcome.detail

    def test_above_max_fails(self) -> None:
        validations = Validations(value_ranges=[ValueRange(column="x", max=10.0)])
        df = pd.DataFrame({"x": [1.0, 50.0]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        assert _outcomes_by_name(outcomes)["value_range:x"].status == "FAIL"

    def test_only_min_declared_no_max_check(self) -> None:
        validations = Validations(value_ranges=[ValueRange(column="x", min=0.0)])
        df = pd.DataFrame({"x": [1.0, 999999.0]})
        outcomes = run_validations(validations, pd.DataFrame(), df)
        assert _outcomes_by_name(outcomes)["value_range:x"].status == "PASS"


class TestSumPreservation:
    def test_matching_sums_pass(self) -> None:
        validations = Validations(
            sum_preservation=[SumPreservation(source_column=0, target_column="x", tolerance=0.0)]
        )
        raw_grid = pd.DataFrame({0: ["1,000", "2,000"]})
        transformed = pd.DataFrame({"x": [1000.0, 2000.0]})
        outcomes = run_validations(validations, raw_grid, transformed)
        outcome = _outcomes_by_name(outcomes)["sum_preservation:0->x"]
        assert outcome.status == "PASS"

    def test_mismatched_sums_fail_beyond_tolerance(self) -> None:
        validations = Validations(
            sum_preservation=[SumPreservation(source_column=0, target_column="x", tolerance=0.01)]
        )
        raw_grid = pd.DataFrame({0: ["1,000", "2,000"]})
        transformed = pd.DataFrame({"x": [1000.0, 1999.0]})  # dropped a dollar somewhere
        outcomes = run_validations(validations, raw_grid, transformed)
        outcome = _outcomes_by_name(outcomes)["sum_preservation:0->x"]
        assert outcome.status == "FAIL"
        assert outcome.detail is not None
        assert "3000" in outcome.detail
        assert "2999" in outcome.detail

    def test_within_tolerance_passes(self) -> None:
        validations = Validations(
            sum_preservation=[SumPreservation(source_column=0, target_column="x", tolerance=0.5)]
        )
        raw_grid = pd.DataFrame({0: ["1,000"]})
        transformed = pd.DataFrame({"x": [1000.2]})
        outcomes = run_validations(validations, raw_grid, transformed)
        assert _outcomes_by_name(outcomes)["sum_preservation:0->x"].status == "PASS"

    def test_blank_source_values_excluded_from_sum(self) -> None:
        validations = Validations(
            sum_preservation=[SumPreservation(source_column=0, target_column="x", tolerance=0.0)]
        )
        raw_grid = pd.DataFrame({0: ["1,000", None, "banner text"]})
        transformed = pd.DataFrame({"x": [1000.0]})
        outcomes = run_validations(validations, raw_grid, transformed)
        assert _outcomes_by_name(outcomes)["sum_preservation:0->x"].status == "PASS"


class TestRunValidationsIntegration:
    def test_all_check_types_run_together(self) -> None:
        validations = Validations(
            row_count=RowCountBounds(min=1),
            key_uniqueness=[KeyUniqueness(columns=["id"])],
            null_policy=[NullPolicy(column="id", nulls_allowed=False)],
            value_ranges=[ValueRange(column="amount", min=0.0)],
            sum_preservation=[
                SumPreservation(source_column=1, target_column="amount", tolerance=0.0)
            ],
        )
        raw_grid = pd.DataFrame({0: [1, 2], 1: ["10", "20"]})
        transformed = pd.DataFrame({"id": [1, 2], "amount": [10.0, 20.0]})
        outcomes = run_validations(validations, raw_grid, transformed)
        assert len(outcomes) == 5
        assert all(o.status == "PASS" for o in outcomes)

    def test_empty_validations_produces_only_row_count_check(self) -> None:
        outcomes = run_validations(Validations(), pd.DataFrame(), pd.DataFrame({"x": [1]}))
        assert len(outcomes) == 1
        assert outcomes[0].check_name == "row_count"
