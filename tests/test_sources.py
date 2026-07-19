"""Tests for src/naru/sources.py and the CSV/TSV path through the runner.

Covers the reader in isolation (cell strings, empty->None, BOM, delimiter,
unsupported format) and end-to-end: a csv source_format artifact runs
through runtime.run() with real fingerprint + lineage, drift halts on a
changed header, and run_golden_test works against a .csv golden input.
"""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from naru import runtime, store
from naru.goldenharness import run_golden_test
from naru.sources import source_workbook_from_bytes

# ---------- reader unit tests ----------


def test_csv_cells_are_strings_and_blank_is_none() -> None:
    wb = source_workbook_from_bytes(b"a,b,c\n1,,x\n", "csv", "data")
    ws = wb["data"]
    assert [c.value for c in ws[1]] == ["a", "b", "c"]
    # blank field -> None (an "empty" cell), numeric text stays a string
    assert [c.value for c in ws[2]] == ["1", None, "x"]


def test_tsv_uses_tab_delimiter() -> None:
    wb = source_workbook_from_bytes(b"a\tb\n1\t2\n", "tsv", "data")
    assert [c.value for c in wb["data"][2]] == ["1", "2"]


def test_utf8_bom_is_stripped_from_first_header() -> None:
    wb = source_workbook_from_bytes("﻿id,label\n1,a\n".encode(), "csv", "data")
    assert wb["data"].cell(row=1, column=1).value == "id"


def test_delimiter_override_via_options() -> None:
    wb = source_workbook_from_bytes(b"a;b\n1;2\n", "csv", "data", {"delimiter": ";"})
    assert [c.value for c in wb["data"][2]] == ["1", "2"]


def test_sheet_name_matches_argument() -> None:
    wb = source_workbook_from_bytes(b"a\n1\n", "csv", "release_calendar")
    assert wb.sheetnames == ["release_calendar"]


def test_unsupported_format_raises() -> None:
    with pytest.raises(ValueError, match="unsupported source_format"):
        source_workbook_from_bytes(b"x", "json", "data")


# ---------- end-to-end CSV pipeline ----------

CSV_MANIFEST = """\
name: csv_pipeline
version: v1
sheet: data
target_table: final_csv
key: [id]
source_format: csv
"""

CSV_FINGERPRINT = """\
{
  "sheet": "data",
  "sheet_index": 0,
  "header_row": 1,
  "columns": [
    {"name": "id", "type": "string", "strictness": "strict"},
    {"name": "label", "type": "string", "strictness": "strict"}
  ]
}
"""

CSV_SCHEMA = """\
import datetime as dt

from pydantic import BaseModel


class SourceRow(BaseModel):
    id: str
    label: str


class TargetRow(BaseModel):
    id: int
    label: str
"""

# CSV cells arrive as strings; the transform declares the real types, exactly
# as it must for messy Excel string cells.
CSV_TRANSFORM = """\
def transform(df):
    df = ops.promote_header(df, header_row=1, column_names=["id", "label"])
    df = ops.drop_empty(df)
    df = ops.coerce_numeric(df, "id")
    return df[["id", "label", "_src_row"]]
"""

CSV_VALIDATIONS = """\
row_count:
  min: 1
key_uniqueness:
  - columns: [id]
"""


def _write_csv_artifact(root: Path, golden_csv: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.yaml").write_text(CSV_MANIFEST)
    (root / "fingerprint.json").write_text(CSV_FINGERPRINT)
    (root / "schema.py").write_text(CSV_SCHEMA)
    (root / "transform.py").write_text(CSV_TRANSFORM)
    (root / "validations.yaml").write_text(CSV_VALIDATIONS)
    (root / "CHANGELOG.md").write_text("# Changelog\n\nv1: initial.\n")
    golden = root / "golden"
    golden.mkdir(exist_ok=True)
    (golden / "input_sample.csv").write_bytes(golden_csv)
    # golden compares the raw transform output, which carries _src_row;
    # the single data row "1,a" sits at source row 2 (row 1 is the header).
    pd.DataFrame({"id": [1.0], "label": ["a"], "_src_row": [2]}).to_parquet(
        golden / "expected_output.parquet", index=False
    )


def test_csv_pipeline_runs_end_to_end(tmp_path: Path) -> None:
    artifact = tmp_path / "art"
    _write_csv_artifact(artifact, b"id,label\n1,a\n")
    db = tmp_path / "db.sqlite"
    input_file = tmp_path / "input.csv"
    input_file.write_bytes(b"id,label\n10,x\n11,y\n")

    result = runtime.run(artifact, input_file, db, tmp_path / "raw")

    assert len(result.row_ids) == 2
    conn = sqlite3.connect(db)
    rows = store.active_rows(conn, "final_csv")
    assert {r["id"] for r in rows} == {10, 11}
    # lineage rows written, tracing to the csv's synthetic sheet
    lineage = conn.execute(
        "SELECT sheet FROM meta_lineage WHERE final_table = 'final_csv'"
    ).fetchall()
    assert {r[0] for r in lineage} == {"data"}
    conn.close()


def test_csv_header_drift_halts(tmp_path: Path) -> None:
    artifact = tmp_path / "art"
    _write_csv_artifact(artifact, b"id,label\n1,a\n")
    db = tmp_path / "db.sqlite"
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"id,LABEL_RENAMED\n10,x\n")  # header drift

    with pytest.raises(runtime.FingerprintDriftError):
        runtime.run(artifact, bad, db, tmp_path / "raw")


def test_csv_golden_test_passes(tmp_path: Path) -> None:
    artifact = tmp_path / "art"
    _write_csv_artifact(artifact, b"id,label\n1,a\n")
    result = run_golden_test(artifact)
    assert result.ok, result.schema_differences + result.value_differences
