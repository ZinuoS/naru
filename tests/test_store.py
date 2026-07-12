"""Unit tests for src/naru/store.py."""

import datetime as dt
import sqlite3
from pathlib import Path

import pytest
from pydantic import BaseModel

from naru import store


class SampleTargetRow(BaseModel):
    auction_date: str
    security_term: str
    high_yield: float


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    store.init_db(connection)
    return connection


class TestSqlColumnsFromModel:
    def test_maps_str_int_float(self) -> None:
        class Row(BaseModel):
            name: str
            count: int
            amount: float

        assert store._sql_columns_from_model(Row) == [
            ("name", "TEXT"),
            ("count", "INTEGER"),
            ("amount", "REAL"),
        ]

    def test_maps_date_to_text(self) -> None:
        class Row(BaseModel):
            d: dt.date

        assert store._sql_columns_from_model(Row) == [("d", "TEXT")]

    def test_unwraps_optional(self) -> None:
        class Row(BaseModel):
            maybe_name: str | None = None

        assert store._sql_columns_from_model(Row) == [("maybe_name", "TEXT")]

    def test_unmapped_type_raises(self) -> None:
        class Row(BaseModel):
            weird: bytes

        with pytest.raises(ValueError, match="no SQL type mapping"):
            store._sql_columns_from_model(Row)

    def test_multi_type_union_is_not_narrowed_and_raises(self) -> None:
        class Row(BaseModel):
            ambiguous: str | int

        with pytest.raises(ValueError, match="no SQL type mapping"):
            store._sql_columns_from_model(Row)


class TestValidateIdentifier:
    def test_valid_identifier_passes(self) -> None:
        store._validate_identifier("final_auction_results", "table name")

    def test_invalid_identifier_raises(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            store._validate_identifier("bad; drop table x", "table name")

    def test_leading_digit_raises(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            store._validate_identifier("1table", "table name")


class TestCreateFinalTable:
    def test_creates_table_with_fixed_and_model_columns(self, conn: sqlite3.Connection) -> None:
        store.create_final_table(conn, "final_test", SampleTargetRow)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(final_test)").fetchall()}
        assert cols == {
            "row_id",
            "auction_date",
            "security_term",
            "high_yield",
            "_run_id",
            "_verification",
            "_superseded_by_run_id",
        }

    def test_invalid_table_name_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            store.create_final_table(conn, "bad name", SampleTargetRow)


class TestRegisterRun:
    def test_registers_and_returns_incrementing_run_ids(self, conn: sqlite3.Connection) -> None:
        run_id_1 = store.register_run(conn, "test_pipeline", "v1")
        run_id_2 = store.register_run(conn, "test_pipeline", "v1")
        assert run_id_2 == run_id_1 + 1

    def test_as_of_none_stored_as_null(self, conn: sqlite3.Connection) -> None:
        run_id = store.register_run(conn, "test_pipeline", "v1")
        (as_of,) = conn.execute(
            "SELECT as_of FROM meta_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert as_of is None

    def test_as_of_given_stored_as_isoformat(self, conn: sqlite3.Connection) -> None:
        run_id = store.register_run(conn, "test_pipeline", "v1", as_of=dt.date(2020, 1, 15))
        (as_of,) = conn.execute(
            "SELECT as_of FROM meta_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert as_of == "2020-01-15"


class TestRegisterRawFile:
    def test_hashes_and_stores_bytes(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        run_id = store.register_run(conn, "test_pipeline", "v1")
        raw_dir = tmp_path / "raw"
        sha256 = store.register_raw_file(conn, raw_dir, b"hello", "input.xlsx", run_id)
        assert (raw_dir / sha256).read_bytes() == b"hello"
        row = conn.execute(
            "SELECT original_name, ingested_run_id FROM raw_files WHERE sha256 = ?", (sha256,)
        ).fetchone()
        assert row == ("input.xlsx", run_id)

    def test_idempotent_for_identical_bytes(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        run_id = store.register_run(conn, "test_pipeline", "v1")
        raw_dir = tmp_path / "raw"
        sha_a = store.register_raw_file(conn, raw_dir, b"same bytes", "a.xlsx", run_id)
        sha_b = store.register_raw_file(conn, raw_dir, b"same bytes", "b.xlsx", run_id)
        assert sha_a == sha_b
        count = conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0]
        assert count == 1


class TestLoadFinalRowsSupersede:
    """The core append-with-supersede contract: a re-run must grow the
    table, not duplicate or overwrite, and superseded rows must remain
    queryable at their original point in time.
    """

    def _load(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        rows: list[dict[str, object]],
        row_markers: list[int],
    ) -> list[int]:
        return store.load_final_rows(
            conn,
            table_name="final_test",
            key_columns=["auction_date"],
            rows=rows,
            row_markers=row_markers,
            run_id=run_id,
            verification="TO_VERIFY",
            file_sha256="deadbeef",
            sheet="Results",
            pipeline_version="v1",
        )

    def test_rerun_grows_table_instead_of_duplicating_or_overwriting(
        self, conn: sqlite3.Connection
    ) -> None:
        store.create_final_table(conn, "final_test", SampleTargetRow)

        run_id_1 = store.register_run(conn, "test_pipeline", "v1")
        self._load(
            conn,
            run_id_1,
            rows=[
                {"auction_date": "2020-01-01", "security_term": "2-Year", "high_yield": 0.01},
                {"auction_date": "2020-01-08", "security_term": "5-Year", "high_yield": 0.02},
            ],
            row_markers=[4, 5],
        )
        assert conn.execute("SELECT COUNT(*) FROM final_test").fetchone()[0] == 2

        run_id_2 = store.register_run(conn, "test_pipeline", "v1")
        self._load(
            conn,
            run_id_2,
            rows=[
                {"auction_date": "2020-01-01", "security_term": "2-Year", "high_yield": 0.05},
            ],
            row_markers=[4],
        )

        # Grew, not overwritten in place, not duplicated as two active rows.
        assert conn.execute("SELECT COUNT(*) FROM final_test").fetchone()[0] == 3

        active = {r["auction_date"]: r for r in store.active_rows(conn, "final_test")}
        assert len(active) == 2
        assert active["2020-01-01"]["high_yield"] == 0.05
        assert active["2020-01-08"]["high_yield"] == 0.02  # untouched by run 2

        # The superseded row still exists with its original data intact.
        superseded = conn.execute(
            "SELECT high_yield, _superseded_by_run_id FROM final_test "
            "WHERE auction_date = '2020-01-01' AND _run_id = ?",
            (run_id_1,),
        ).fetchone()
        assert superseded == (0.01, run_id_2)

    def test_superseded_rows_remain_queryable_point_in_time(self, conn: sqlite3.Connection) -> None:
        store.create_final_table(conn, "final_test", SampleTargetRow)

        run_id_1 = store.register_run(conn, "test_pipeline", "v1")
        self._load(
            conn,
            run_id_1,
            rows=[
                {"auction_date": "2020-01-01", "security_term": "2-Year", "high_yield": 0.01},
                {"auction_date": "2020-01-08", "security_term": "5-Year", "high_yield": 0.02},
            ],
            row_markers=[4, 5],
        )

        run_id_2 = store.register_run(conn, "test_pipeline", "v1")
        self._load(
            conn,
            run_id_2,
            rows=[
                {"auction_date": "2020-01-01", "security_term": "2-Year", "high_yield": 0.05},
            ],
            row_markers=[4],
        )

        # As of run 1: the original, uncorrected value is what was true then.
        as_of_run_1 = {
            r["auction_date"]: r["high_yield"]
            for r in store.rows_as_of(conn, "final_test", run_id_1)
        }
        assert as_of_run_1 == {"2020-01-01": 0.01, "2020-01-08": 0.02}

        # As of run 2: the correction is visible, matching current active rows.
        as_of_run_2 = {
            r["auction_date"]: r["high_yield"]
            for r in store.rows_as_of(conn, "final_test", run_id_2)
        }
        assert as_of_run_2 == {"2020-01-01": 0.05, "2020-01-08": 0.02}

    def test_writes_one_lineage_row_per_output_row(self, conn: sqlite3.Connection) -> None:
        store.create_final_table(conn, "final_test", SampleTargetRow)
        run_id = store.register_run(conn, "test_pipeline", "v1")
        row_ids = self._load(
            conn,
            run_id,
            rows=[
                {"auction_date": "2020-01-01", "security_term": "2-Year", "high_yield": 0.01},
            ],
            row_markers=[7],
        )
        lineage = conn.execute(
            "SELECT final_table, row_id, file_sha256, sheet, "
            "source_row_start, source_row_end, pipeline_version, run_id "
            "FROM meta_lineage WHERE row_id = ?",
            (row_ids[0],),
        ).fetchone()
        assert lineage == ("final_test", row_ids[0], "deadbeef", "Results", 7, 7, "v1", run_id)

    def test_new_run_ids_are_sequential_across_loads(self, conn: sqlite3.Connection) -> None:
        store.create_final_table(conn, "final_test", SampleTargetRow)
        run_id = store.register_run(conn, "test_pipeline", "v1")
        row_ids = self._load(
            conn,
            run_id,
            rows=[
                {"auction_date": "2020-01-01", "security_term": "2-Year", "high_yield": 0.01},
                {"auction_date": "2020-01-08", "security_term": "5-Year", "high_yield": 0.02},
            ],
            row_markers=[4, 5],
        )
        assert row_ids == [1, 2]
