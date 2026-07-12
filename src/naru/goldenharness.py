"""Golden harness: rerun an artifact's transform against golden/input_sample.xlsx
and compare against golden/expected_output.parquet.

Schema drift (columns/dtypes differ) and value drift (same schema, different
data) are reported separately -- they mean different things to a reviewer.
Schema drift usually means the transform's shape changed on purpose (needs a
CHANGELOG entry and a deliberate refreeze, see scripts/refreeze.py). Value
drift means either a real bug or a legitimate output change -- either way,
worth a second look before refreezing.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from naru.artifact import load_artifact
from naru.runtime import read_raw_grid


@dataclass
class GoldenTestResult:
    ok: bool
    schema_differences: list[str] = field(default_factory=list)
    value_differences: list[str] = field(default_factory=list)


def _compare_schema(actual: pd.DataFrame, expected: pd.DataFrame) -> list[str]:
    differences = []
    for column in expected.columns:
        if column not in actual.columns:
            differences.append(f"column {column!r} missing from actual output")
    for column in actual.columns:
        if column not in expected.columns:
            differences.append(f"column {column!r} present in actual output but not in golden")
    for column in expected.columns:
        if column in actual.columns and str(actual[column].dtype) != str(expected[column].dtype):
            differences.append(
                f"column {column!r} dtype mismatch: "
                f"golden has {expected[column].dtype}, actual has {actual[column].dtype}"
            )
    return differences


def _compare_values(actual: pd.DataFrame, expected: pd.DataFrame) -> list[str]:
    if len(actual) != len(expected):
        return [f"row count mismatch: golden has {len(expected)}, actual has {len(actual)}"]

    differences = []
    for column in expected.columns:
        for idx in expected.index:
            expected_value = expected.at[idx, column]
            actual_value = actual.at[idx, column]
            if pd.isna(expected_value) and pd.isna(actual_value):
                continue
            if expected_value != actual_value:
                differences.append(
                    f"row {idx}, column {column!r}: "
                    f"golden has {expected_value!r}, actual has {actual_value!r}"
                )
    return differences


def run_golden_test(artifact_path: Path) -> GoldenTestResult:
    """Rerun transform() against golden/input_sample.xlsx and compare
    against the frozen golden/expected_output.parquet.

    Schema comparison runs first and short-circuits: comparing values
    cell-by-cell when the columns themselves don't match isn't meaningful.
    """
    artifact = load_artifact(artifact_path)
    wb = load_workbook(artifact_path / "golden" / "input_sample.xlsx", data_only=True)
    raw_grid = read_raw_grid(wb, artifact.manifest.sheet)
    actual = artifact.transform(raw_grid)
    expected = pd.read_parquet(artifact_path / "golden" / "expected_output.parquet")

    schema_differences = _compare_schema(actual, expected)
    if schema_differences:
        return GoldenTestResult(ok=False, schema_differences=schema_differences)

    value_differences = _compare_values(actual, expected)
    return GoldenTestResult(ok=not value_differences, value_differences=value_differences)
