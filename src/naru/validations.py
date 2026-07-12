"""Output-contract validation engine, per docs/spec.md §2.1/§2.5.

Every check declared in validations.yaml runs against real output rows.
Results persist to meta_validation_results regardless of outcome -- even
a failed run leaves a full audit trail of what was checked and why it
failed. Any FAIL aborts the load before any final-table row is written
(enforced in src/naru/runtime.py; this module only decides pass/fail).
"""

from dataclasses import dataclass

import pandas as pd

from naru import ops
from naru.artifact import Validations


@dataclass
class ValidationOutcome:
    check_name: str
    status: str  # "PASS" or "FAIL"
    detail: str | None = None


def _check_row_count(validations: Validations, transformed: pd.DataFrame) -> ValidationOutcome:
    bounds = validations.row_count
    n = len(transformed)
    if bounds.min is not None and n < bounds.min:
        return ValidationOutcome("row_count", "FAIL", f"{n} rows < minimum {bounds.min}")
    if bounds.max is not None and n > bounds.max:
        return ValidationOutcome("row_count", "FAIL", f"{n} rows > maximum {bounds.max}")
    return ValidationOutcome("row_count", "PASS", f"{n} rows within bounds")


def _check_key_uniqueness(
    validations: Validations, transformed: pd.DataFrame
) -> list[ValidationOutcome]:
    outcomes = []
    for spec in validations.key_uniqueness:
        check_name = f"key_uniqueness:{','.join(spec.columns)}"
        duplicated = transformed.duplicated(subset=spec.columns, keep=False)
        if duplicated.any():
            dupes = (
                transformed.loc[duplicated, spec.columns]
                .drop_duplicates()
                .to_dict(orient="records")
            )
            outcomes.append(ValidationOutcome(check_name, "FAIL", f"duplicate keys: {dupes}"))
        else:
            outcomes.append(ValidationOutcome(check_name, "PASS", "no duplicate keys"))
    return outcomes


def _check_null_policy(
    validations: Validations, transformed: pd.DataFrame
) -> list[ValidationOutcome]:
    outcomes = []
    for spec in validations.null_policy:
        check_name = f"null_policy:{spec.column}"
        null_count = int(transformed[spec.column].isna().sum())
        if not spec.nulls_allowed and null_count > 0:
            outcomes.append(
                ValidationOutcome(
                    check_name, "FAIL", f"{null_count} null value(s) found, none allowed"
                )
            )
        else:
            outcomes.append(
                ValidationOutcome(
                    check_name, "PASS", f"{null_count} null value(s), policy satisfied"
                )
            )
    return outcomes


def _check_value_ranges(
    validations: Validations, transformed: pd.DataFrame
) -> list[ValidationOutcome]:
    outcomes = []
    for spec in validations.value_ranges:
        check_name = f"value_range:{spec.column}"
        series = transformed[spec.column]
        violations = pd.Series(False, index=series.index)
        if spec.min is not None:
            violations |= series < spec.min
        if spec.max is not None:
            violations |= series > spec.max
        if violations.any():
            bad_values = series[violations].tolist()
            outcomes.append(
                ValidationOutcome(
                    check_name,
                    "FAIL",
                    f"out-of-range values (expected [{spec.min}, {spec.max}]): {bad_values}",
                )
            )
        else:
            outcomes.append(
                ValidationOutcome(check_name, "PASS", f"all values within [{spec.min}, {spec.max}]")
            )
    return outcomes


def _check_sum_preservation(
    validations: Validations, raw_grid: pd.DataFrame, transformed: pd.DataFrame
) -> list[ValidationOutcome]:
    outcomes = []
    for spec in validations.sum_preservation:
        check_name = f"sum_preservation:{spec.source_column}->{spec.target_column}"
        source_parsed = ops.coerce_numeric(raw_grid, spec.source_column, allow_null=True)
        source_sum = source_parsed[spec.source_column].sum()
        target_sum = transformed[spec.target_column].sum()
        diff = abs(source_sum - target_sum)
        if diff > spec.tolerance:
            outcomes.append(
                ValidationOutcome(
                    check_name,
                    "FAIL",
                    f"source sum {source_sum} vs target sum {target_sum} "
                    f"(diff {diff} > tolerance {spec.tolerance})",
                )
            )
        else:
            outcomes.append(
                ValidationOutcome(
                    check_name, "PASS", f"source sum {source_sum} ~= target sum {target_sum}"
                )
            )
    return outcomes


def run_validations(
    validations: Validations, raw_grid: pd.DataFrame, transformed: pd.DataFrame
) -> list[ValidationOutcome]:
    """Run every check declared in validations.yaml against real output rows.

    Takes both the raw grid (pre-transform) and the transformed output
    (post-transform): sum_preservation is the only check that needs both,
    but every check runs from this one entry point for a single, obvious
    place runtime.py calls into.
    """
    outcomes = [_check_row_count(validations, transformed)]
    outcomes += _check_key_uniqueness(validations, transformed)
    outcomes += _check_null_policy(validations, transformed)
    outcomes += _check_value_ranges(validations, transformed)
    outcomes += _check_sum_preservation(validations, raw_grid, transformed)
    return outcomes
