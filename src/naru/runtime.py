"""Runner: naru run <artifact> <input> -- spec.md §2's runtime sequence.

Deterministic, offline: no network, no wall-clock reads except the
explicit `as_of` parameter, which is stored exactly as given and never
auto-filled from the clock (CLAUDE.md prime directive 1).

Fingerprint checking (step a) and output-contract validation (step d) are
both stubs returning "ok" this week -- real enforcement is Week 4, matching
src/naru/artifact.py's own scope note for fingerprint.json/validations.yaml.
Per-row TargetRow schema conformance IS checked for real here, though --
that's a distinct, narrower check (does this row match its declared
shape?) from the validations.yaml business-rule engine (row-count bounds,
sum preservation, etc.) that stays deferred.
"""

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook

from naru import store
from naru.artifact import Artifact, load_artifact


class RuntimeCheckError(Exception):
    """A runtime-sequence check failed: fingerprint, output contract, or
    per-row schema conformance.
    """


@dataclass
class CheckResult:
    ok: bool
    report: dict[str, Any] | None = None


def _check_fingerprint(artifact: Artifact, raw_bytes: bytes) -> CheckResult:
    """Stub: always ok. Real drift detection is Week 4 (spec.md §2.3)."""
    return CheckResult(ok=True)


def _check_output_contract(artifact: Artifact, df: pd.DataFrame) -> CheckResult:
    """Stub: always ok. The validations.yaml engine is Week 4."""
    return CheckResult(ok=True)


def _read_raw_grid(path: Path, sheet_name: str) -> pd.DataFrame:
    """I/O at the edge: read one sheet's raw cell grid into a DataFrame,
    tagging each row with its 1-indexed source row number in `_src_row`.
    """
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet_name]
    records: list[dict[int | str, Any]] = []
    for src_row, row in enumerate(ws.iter_rows(), start=1):
        record: dict[int | str, Any] = {i: cell.value for i, cell in enumerate(row)}
        record["_src_row"] = src_row
        records.append(record)
    return pd.DataFrame.from_records(records)


@dataclass
class RunResult:
    run_id: int
    row_ids: list[int]
    fingerprint_check: CheckResult
    output_check: CheckResult


def run(
    artifact_path: Path,
    input_path: Path,
    db_path: Path,
    raw_dir: Path,
    as_of: dt.date | None = None,
) -> RunResult:
    """Execute a pipeline artifact against an input file.

    Sequence (spec.md §2):
      a. fingerprint check (stub this week)
      b. raw zone: store bytes + SHA256
      c. apply frozen transforms
      d. output contract validation (stub this week, plus real per-row
         TargetRow conformance)
      e. load SQLite + lineage rows
      f. register the run

    The run is registered first here, ahead of steps b-e, not last: every
    later step needs a run_id to attach records to. spec.md's a-f
    lettering reads as a narrative walkthrough of what happens, not an
    enforced literal order.
    """
    artifact = load_artifact(artifact_path)

    conn = sqlite3.connect(db_path)
    store.init_db(conn)
    store.create_final_table(conn, artifact.manifest.target_table, artifact.target_row)
    run_id = store.register_run(conn, artifact.manifest.name, artifact.manifest.version, as_of)

    raw_bytes = input_path.read_bytes()
    fingerprint_check = _check_fingerprint(artifact, raw_bytes)
    if not fingerprint_check.ok:  # pragma: no cover -- stub always returns ok this week
        raise RuntimeCheckError(f"fingerprint check failed: {fingerprint_check.report}")

    file_sha256 = store.register_raw_file(conn, raw_dir, raw_bytes, input_path.name, run_id)

    raw_grid = _read_raw_grid(input_path, artifact.manifest.sheet)
    transformed = artifact.transform(raw_grid)

    output_check = _check_output_contract(artifact, transformed)
    if not output_check.ok:  # pragma: no cover -- stub always returns ok this week
        raise RuntimeCheckError(f"output contract check failed: {output_check.report}")

    business_columns = [c for c in transformed.columns if c != "_src_row"]
    target_fields = set(artifact.target_row.model_fields)
    if set(business_columns) != target_fields:
        raise RuntimeCheckError(
            f"transform output columns {sorted(business_columns)} don't match "
            f"TargetRow fields {sorted(target_fields)}"
        )

    rows: list[dict[str, object]] = []
    row_markers: list[int] = []
    for record in transformed.to_dict(orient="records"):
        validated = artifact.target_row(**{k: record[k] for k in business_columns})
        rows.append(validated.model_dump(mode="json"))
        row_markers.append(record["_src_row"])

    row_ids = store.load_final_rows(
        conn,
        table_name=artifact.manifest.target_table,
        key_columns=artifact.manifest.key,
        rows=rows,
        row_markers=row_markers,
        run_id=run_id,
        verification="TO_VERIFY",
        file_sha256=file_sha256,
        sheet=artifact.manifest.sheet,
        pipeline_version=artifact.manifest.version,
    )

    conn.close()
    return RunResult(
        run_id=run_id,
        row_ids=row_ids,
        fingerprint_check=fingerprint_check,
        output_check=output_check,
    )


if __name__ == "__main__":  # pragma: no cover
    import sys

    result = run(
        artifact_path=Path(sys.argv[1]),
        input_path=Path(sys.argv[2]),
        db_path=Path("naru.sqlite"),
        raw_dir=Path(".naru/raw"),
    )
    print(f"run_id={result.run_id} rows_loaded={len(result.row_ids)}")
