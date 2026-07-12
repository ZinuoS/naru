"""SQLite storage layer: raw/final/meta zones, per docs/spec.md §2.5.

SQLite has no real schema-namespace feature without ATTACH (see tracer.py's
original note on this); zones are mirrored as table-name prefixes:
`raw_files`, `meta_runs`, `meta_lineage`, `meta_validation_results`. Final
tables are named per-pipeline (manifest.yaml's `target_table`) and created
dynamically from the pipeline's TargetRow pydantic model.

Loads are append-with-supersede, never in-place update: a new row sharing
an existing active row's natural key marks that old row's
`_superseded_by_run_id` (bookkeeping only -- its data columns are never
touched) and inserts a fresh row. This is a Type-2-slowly-changing-
dimension pattern: "current" is `_superseded_by_run_id IS NULL`, and any
past point in time stays queryable via `_run_id`/`_superseded_by_run_id`.
"""

import datetime as dt
import hashlib
import re
import sqlite3
import types
import typing
from pathlib import Path

from pydantic import BaseModel

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SQL_TYPE_BY_PYTHON_TYPE: dict[type, str] = {
    str: "TEXT",
    int: "INTEGER",
    float: "REAL",
    bool: "INTEGER",
    dt.date: "TEXT",
    dt.datetime: "TEXT",
}

_FIXED_DDL = """
CREATE TABLE IF NOT EXISTS meta_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_name TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    as_of TEXT
);

CREATE TABLE IF NOT EXISTS raw_files (
    sha256 TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    ingested_run_id INTEGER NOT NULL REFERENCES meta_runs (run_id)
);

CREATE TABLE IF NOT EXISTS meta_lineage (
    final_table TEXT NOT NULL,
    row_id INTEGER NOT NULL,
    file_sha256 TEXT NOT NULL,
    sheet TEXT NOT NULL,
    source_row_start INTEGER NOT NULL,
    source_row_end INTEGER NOT NULL,
    pipeline_version TEXT NOT NULL,
    run_id INTEGER NOT NULL REFERENCES meta_runs (run_id),
    PRIMARY KEY (final_table, row_id)
);

CREATE TABLE IF NOT EXISTS meta_validation_results (
    run_id INTEGER NOT NULL REFERENCES meta_runs (run_id),
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT
);
"""


def _validate_identifier(name: str, context: str) -> None:
    """Fail loudly on a table/column name that isn't a safe SQL identifier.

    Not a SQL-injection defense against hostile input (artifacts are
    locally-authored, human-reviewed config, not multi-tenant input) --
    just a load-bearing sanity check before splicing a name into DDL/DML.
    """
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"{context} {name!r} is not a valid SQL identifier")


def init_db(conn: sqlite3.Connection) -> None:
    """Create the fixed meta/raw tables if they don't already exist."""
    conn.executescript(_FIXED_DDL)


def _sql_columns_from_model(model: type[BaseModel]) -> list[tuple[str, str]]:
    """Map a pydantic model's fields to (column_name, sql_type) pairs.

    >>> from pydantic import BaseModel
    >>> class Row(BaseModel):
    ...     name: str
    ...     amount: float
    >>> _sql_columns_from_model(Row)
    [('name', 'TEXT'), ('amount', 'REAL')]
    """
    columns = []
    for name, field in model.model_fields.items():
        annotation = field.annotation
        origin = typing.get_origin(annotation)
        if origin is typing.Union or origin is types.UnionType:
            non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
            if len(non_none) == 1:
                annotation = non_none[0]
        sql_type = _SQL_TYPE_BY_PYTHON_TYPE.get(annotation)  # type: ignore[arg-type]
        if sql_type is None:
            raise ValueError(f"no SQL type mapping for field {name!r} of type {annotation!r}")
        columns.append((name, sql_type))
    return columns


def create_final_table(
    conn: sqlite3.Connection, table_name: str, target_row: type[BaseModel]
) -> None:
    """Create a final-zone table from a pipeline's TargetRow model, with
    the fixed provenance/supersede columns every final table carries.
    """
    _validate_identifier(table_name, "table name")
    columns = _sql_columns_from_model(target_row)
    for name, _ in columns:
        _validate_identifier(name, "column name")
    column_defs = ",\n    ".join(f"{name} {sql_type}" for name, sql_type in columns)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            row_id INTEGER PRIMARY KEY,
            {column_defs},
            _run_id INTEGER NOT NULL REFERENCES meta_runs (run_id),
            _verification TEXT NOT NULL,
            _superseded_by_run_id INTEGER REFERENCES meta_runs (run_id)
        )
        """
    )


def register_run(
    conn: sqlite3.Connection,
    pipeline_name: str,
    pipeline_version: str,
    as_of: dt.date | None = None,
) -> int:
    """Register a new run, returning its run_id.

    `as_of` is stored exactly as given -- None stays None, never
    backfilled from the wall clock (CLAUDE.md prime directive 1).
    """
    cur = conn.execute(
        "INSERT INTO meta_runs (pipeline_name, pipeline_version, as_of) VALUES (?, ?, ?)",
        (pipeline_name, pipeline_version, as_of.isoformat() if as_of else None),
    )
    conn.commit()
    run_id = cur.lastrowid
    assert run_id is not None
    return run_id


def register_raw_file(
    conn: sqlite3.Connection,
    raw_dir: Path,
    data: bytes,
    original_name: str,
    run_id: int,
) -> str:
    """Hash and content-address raw file bytes; register in raw_files."""
    sha256 = hashlib.sha256(data).hexdigest()
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / sha256
    if not dest.exists():
        dest.write_bytes(data)
    conn.execute(
        "INSERT OR REPLACE INTO raw_files (sha256, original_name, ingested_run_id) "
        "VALUES (?, ?, ?)",
        (sha256, original_name, run_id),
    )
    conn.commit()
    return sha256


def load_final_rows(
    conn: sqlite3.Connection,
    table_name: str,
    key_columns: list[str],
    rows: list[dict[str, object]],
    row_markers: list[int],
    run_id: int,
    verification: str,
    file_sha256: str,
    sheet: str,
    pipeline_version: str,
) -> list[int]:
    """Load rows into a final table with append-with-supersede semantics
    and write one meta_lineage row per output row. Returns the new row_ids.

    For each row, any existing *active* row (`_superseded_by_run_id IS
    NULL`) sharing its natural key gets `_superseded_by_run_id` set to
    this run_id -- its data columns are never touched, never deleted. The
    new row is always inserted fresh.
    """
    _validate_identifier(table_name, "table name")
    cur = conn.cursor()
    (max_row_id,) = cur.execute(f"SELECT COALESCE(MAX(row_id), 0) FROM {table_name}").fetchone()
    next_row_id = 1 + max_row_id

    key_conditions = " AND ".join(f"{c} = ?" for c in key_columns)
    new_row_ids = []
    for offset, row in enumerate(rows):
        key_values = tuple(row[c] for c in key_columns)
        cur.execute(
            f"UPDATE {table_name} SET _superseded_by_run_id = ? "
            f"WHERE _superseded_by_run_id IS NULL AND {key_conditions}",
            (run_id, *key_values),
        )

        row_id = next_row_id + offset
        columns = list(row.keys())
        column_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        cur.execute(
            f"INSERT INTO {table_name} "
            f"(row_id, {column_list}, _run_id, _verification, _superseded_by_run_id) "
            f"VALUES (?, {placeholders}, ?, ?, NULL)",
            (row_id, *(row[c] for c in columns), run_id, verification),
        )

        src_row = row_markers[offset]
        cur.execute(
            """
            INSERT INTO meta_lineage
                (final_table, row_id, file_sha256, sheet,
                 source_row_start, source_row_end, pipeline_version, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (table_name, row_id, file_sha256, sheet, src_row, src_row, pipeline_version, run_id),
        )
        new_row_ids.append(row_id)
    conn.commit()
    return new_row_ids


def active_rows(conn: sqlite3.Connection, table_name: str) -> list[dict[str, object]]:
    """Currently-active rows in a final table (not superseded by any run)."""
    _validate_identifier(table_name, "table name")
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    result = cur.execute(
        f"SELECT * FROM {table_name} WHERE _superseded_by_run_id IS NULL"
    ).fetchall()
    return [dict(r) for r in result]


def rows_as_of(conn: sqlite3.Connection, table_name: str, run_id: int) -> list[dict[str, object]]:
    """Rows visible as of a given run_id: created at or before it, and not
    yet superseded at that point -- the point-in-time view.
    """
    _validate_identifier(table_name, "table name")
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    result = cur.execute(
        f"SELECT * FROM {table_name} WHERE _run_id <= ? "
        f"AND (_superseded_by_run_id IS NULL OR _superseded_by_run_id > ?)",
        (run_id, run_id),
    ).fetchall()
    return [dict(r) for r in result]
