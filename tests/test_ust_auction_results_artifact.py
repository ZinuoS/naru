"""End-to-end tests for pipelines/ust_auction_results/v1 -- the first real
artifact, ported from the retired tracer.py bullet.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from naru import store
from naru.artifact import load_artifact
from naru.runtime import read_raw_grid, run

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "pipelines" / "ust_auction_results" / "v1"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "ust_lite.xlsx"


class TestArtifactLoadsAndTransforms:
    def test_artifact_loads(self) -> None:
        artifact = load_artifact(ARTIFACT_PATH)
        assert artifact.manifest.name == "ust_auction_results"
        assert artifact.manifest.target_table == "final_auction_results"
        assert artifact.manifest.key == ["auction_date"]

    def test_transform_matches_frozen_golden(self) -> None:
        artifact = load_artifact(ARTIFACT_PATH)
        wb = load_workbook(FIXTURE_PATH, data_only=True)
        raw_grid = read_raw_grid(wb, artifact.manifest.sheet)
        actual = artifact.transform(raw_grid)
        expected = pd.read_parquet(ARTIFACT_PATH / "golden" / "expected_output.parquet")
        pd.testing.assert_frame_equal(actual, expected)


class TestRunEndToEnd:
    def test_run_loads_forty_rows_with_working_lineage_join(self, tmp_path: Path) -> None:
        db_path = tmp_path / "naru.sqlite"
        raw_dir = tmp_path / "raw"

        result = run(
            artifact_path=ARTIFACT_PATH,
            input_path=FIXTURE_PATH,
            db_path=db_path,
            raw_dir=raw_dir,
        )
        assert len(result.row_ids) == 40

        conn = sqlite3.connect(db_path)
        joined = conn.execute(
            """
            SELECT f.row_id, f.auction_date, f.security_term, f.high_yield,
                   l.file_sha256, l.sheet, l.source_row_start, l.source_row_end
            FROM final_auction_results AS f
            JOIN meta_lineage AS l
                ON l.final_table = 'final_auction_results' AND l.row_id = f.row_id
            ORDER BY f.row_id
            """
        ).fetchall()
        assert len(joined) == 40
        first = joined[0]
        assert first[1] == "2019-01-15"
        assert first[5] == "Results"
        assert first[6] == first[7] == 4  # source_row_start == source_row_end == row 4

    def test_rerun_against_same_file_supersedes_not_duplicates(self, tmp_path: Path) -> None:
        db_path = tmp_path / "naru.sqlite"
        raw_dir = tmp_path / "raw"

        first_run = run(
            artifact_path=ARTIFACT_PATH, input_path=FIXTURE_PATH, db_path=db_path, raw_dir=raw_dir
        )
        run(artifact_path=ARTIFACT_PATH, input_path=FIXTURE_PATH, db_path=db_path, raw_dir=raw_dir)

        conn = sqlite3.connect(db_path)
        total = conn.execute("SELECT COUNT(*) FROM final_auction_results").fetchone()[0]
        assert total == 80  # grew, not overwritten

        active = store.active_rows(conn, "final_auction_results")
        assert len(active) == 40  # not duplicated as two active sets

        superseded = conn.execute(
            "SELECT COUNT(*) FROM final_auction_results WHERE _superseded_by_run_id IS NOT NULL"
        ).fetchone()[0]
        assert superseded == 40

        # Point-in-time: as of run 1, the original 40 rows are what was visible.
        as_of_run_1 = store.rows_as_of(conn, "final_auction_results", first_run.run_id)
        assert len(as_of_run_1) == 40


def test_cli_entry_point_runs_via_python_dash_m(tmp_path: Path) -> None:
    """Exercises the exact `python -m naru run` invocation the exit test
    uses, in an isolated tmp_path so it never touches the repo root.
    """
    db_path = tmp_path / "naru.sqlite"
    (tmp_path / "naru_run_input.xlsx").write_bytes(FIXTURE_PATH.read_bytes())

    result = subprocess.run(
        [sys.executable, "-m", "naru", "run", str(ARTIFACT_PATH), "naru_run_input.xlsx"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "run_id=1 rows_loaded=40" in result.stdout
    assert db_path.exists()
