"""Constrained op API: pure `DataFrame -> DataFrame` transforms.

Per docs/spec.md §2.4, a pipeline artifact's transform.py may compose only
ops from this module. Per docs/adr/0001-lineage-carrier.md, every op that
keeps or drops rows must preserve the row-provenance column (`_src_row` by
default) unchanged; ops that duplicate rows must duplicate it along with
the row.
"""

import re
from collections.abc import Callable
from typing import Literal

import pandas as pd
from openpyxl.utils.datetime import from_excel

DEFAULT_ROW_MARKER = "_src_row"

_PAREN_NEGATIVE_RE = re.compile(r"^\((.*)\)$")

_VERIFICATION_VALUES = {"VERIFIED", "TO_VERIFY", "DERIVED"}


def promote_header(
    df: pd.DataFrame,
    header_row: int,
    column_names: list[str],
    row_marker: str = DEFAULT_ROW_MARKER,
) -> pd.DataFrame:
    """Drop all rows at or above `header_row` and assign `column_names` to
    the remaining columns by position.

    Column names are assigned by position rather than read from the header
    row's own text on purpose: a header row can carry merged cells or other
    artifacts that make its literal text unreliable, so the caller supplies
    the names it knows are correct for this source.

    >>> df = pd.DataFrame({0: ["h", "a"], 1: ["h2", "b"], "_src_row": [1, 2]})
    >>> promote_header(df, header_row=1, column_names=["x", "y"])
       x  y  _src_row
    0  a  b         2
    """
    if len(column_names) != len(df.columns) - 1:
        raise ValueError(
            f"expected {len(df.columns) - 1} column names for "
            f"{len(df.columns) - 1} data columns, got {len(column_names)}"
        )
    data_rows = df[df[row_marker] > header_row].copy()
    other_cols = [c for c in df.columns if c != row_marker]
    data_rows = data_rows.rename(columns=dict(zip(other_cols, column_names, strict=True)))
    return data_rows.reset_index(drop=True)


def drop_empty(df: pd.DataFrame, row_marker: str = DEFAULT_ROW_MARKER) -> pd.DataFrame:
    """Drop rows where every non-marker column is null.

    >>> df = pd.DataFrame({"x": ["a", None], "_src_row": [1, 2]})
    >>> drop_empty(df)
       x  _src_row
    0  a         1
    """
    business_cols = [c for c in df.columns if c != row_marker]
    return df.dropna(subset=business_cols, how="all").reset_index(drop=True)


def _parse_numeric_token(raw: object) -> float | None:
    """Parse one messy numeric token; return None if blank or unparseable."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    negative = False
    paren_match = _PAREN_NEGATIVE_RE.match(text)
    if paren_match:
        negative = True
        text = paren_match.group(1).strip()
    percent = False
    if text.endswith("%"):
        percent = True
        text = text[:-1].strip()
    text = text.replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    if percent:
        value /= 100.0
    if negative:
        value = -value
    return value


def coerce_numeric(df: pd.DataFrame, column: str, allow_null: bool = False) -> pd.DataFrame:
    """Coerce a messy numeric string column to float64.

    Handles, in combination: surrounding whitespace, comma thousands
    separators, a trailing '%' (divides by 100), and parens-as-negative
    accounting notation (e.g. '(1,234.5)' -> -1234.5). Blank or unparseable
    values raise ValueError by default -- pass allow_null=True to convert
    them to NaN instead.

    >>> coerce_numeric(pd.DataFrame({"x": ["(1,234.50)"]}), "x")
            x
    0 -1234.5
    """
    df = df.copy()
    parsed: list[float] = []
    for idx, raw in df[column].items():
        value = _parse_numeric_token(raw)
        if value is None:
            if allow_null:
                parsed.append(float("nan"))
                continue
            raise ValueError(
                f"coerce_numeric: column {column!r} row index {idx} has an "
                f"unparseable value {raw!r}; pass allow_null=True to convert "
                "unparseable values to NaN instead of failing"
            )
        parsed.append(value)
    df[column] = parsed
    return df


def coerce_date(df: pd.DataFrame, column: str, fmt: str | None = None) -> pd.DataFrame:
    """Parse a date column into ISO-8601 date strings.

    `fmt` given: parse strings in that strptime format (e.g. '%m/%d/%Y').
    `fmt=None`: treat values as raw Excel date serial numbers. The mode is
    always caller-declared, never auto-detected -- dd/mm vs mm/dd string
    ambiguity must be resolved by whoever knows the source, not guessed.

    >>> coerce_date(pd.DataFrame({"x": ["01/15/2019"]}), "x", fmt="%m/%d/%Y")
                x
    0  2019-01-15
    >>> coerce_date(pd.DataFrame({"x": [43480]}), "x")
                x
    0  2019-01-15
    """
    df = df.copy()
    if fmt is None:
        df[column] = df[column].apply(lambda v: from_excel(v).date().isoformat())
    else:
        df[column] = pd.to_datetime(df[column], format=fmt).dt.date.astype(str)
    return df


def select_sheet(sheets: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    """Select one sheet's DataFrame out of an already-loaded workbook dict.

    No file I/O here: the workbook is read at the edge (the runner), and
    this op just picks one already-loaded sheet out by name.

    >>> select_sheet({"Results": pd.DataFrame({"x": [1]})}, "Results")
       x
    0  1
    """
    if name not in sheets:
        available = ", ".join(sorted(sheets))
        raise KeyError(f"select_sheet: sheet {name!r} not found; available sheets: {available}")
    return sheets[name]


def unpivot(
    df: pd.DataFrame,
    id_vars: list[str],
    value_vars: list[str],
    var_name: str,
    value_name: str,
    row_marker: str = DEFAULT_ROW_MARKER,
) -> pd.DataFrame:
    """Reshape wide columns into long (var, value) pairs, duplicating
    `row_marker` across every exploded row so provenance stays intact.

    >>> df = pd.DataFrame({"id": ["a"], "q1": [1], "q2": [2], "_src_row": [5]})
    >>> unpivot(df, ["id"], ["q1", "q2"], var_name="quarter", value_name="amount")
      id  _src_row quarter  amount
    0  a         5      q1       1
    1  a         5      q2       2
    """
    full_id_vars = id_vars if row_marker in id_vars else [*id_vars, row_marker]
    return pd.melt(
        df, id_vars=full_id_vars, value_vars=value_vars, var_name=var_name, value_name=value_name
    )


def split_column(
    df: pd.DataFrame,
    column: str,
    into: list[str],
    pattern: str,
    row_marker: str = DEFAULT_ROW_MARKER,
) -> pd.DataFrame:
    r"""Split one string column into multiple columns via a regex with one
    capture group per name in `into`, replacing `column` in place. Rows
    that don't match `pattern` are a hard failure, listed by `row_marker`.

    >>> df = pd.DataFrame({"x": ["10Y-2.5"], "_src_row": [3]})
    >>> split_column(df, "x", into=["term", "rate"], pattern=r"(\d+Y)-(\d+\.\d+)")
      term rate  _src_row
    0  10Y  2.5         3
    """
    df = df.copy()
    extracted = df[column].str.extract(pattern, expand=True)
    if extracted.shape[1] != len(into):
        raise ValueError(
            f"split_column: pattern has {extracted.shape[1]} capture groups, "
            f"expected {len(into)} to match `into`"
        )
    extracted.columns = pd.Index(into)
    unmatched = extracted.isna().any(axis=1)
    if unmatched.any():
        bad_rows = (
            df.loc[unmatched, row_marker].tolist()
            if row_marker in df.columns
            else df.index[unmatched].tolist()
        )
        raise ValueError(f"split_column: pattern did not match rows with {row_marker}={bad_rows}")
    position = df.columns.get_loc(column)
    before = df.columns[:position]
    after = df.columns[position + 1 :]
    ordered = pd.concat([df[before], extracted, df[after]], axis=1)
    return ordered.reset_index(drop=True)


def map_values(
    df: pd.DataFrame,
    column: str,
    mapping: dict[object, object],
    on_missing: Literal["fail", "keep"] = "fail",
) -> pd.DataFrame:
    """Remap a column's values through a lookup table.

    Unmapped values raise ValueError by default -- pass on_missing='keep'
    to leave them unchanged instead.

    >>> map_values(pd.DataFrame({"x": ["A", "B"]}), "x", {"A": "Alpha", "B": "Beta"})
           x
    0  Alpha
    1   Beta
    """
    df = df.copy()
    unmapped = ~df[column].isin(mapping.keys())
    if on_missing == "fail" and unmapped.any():
        bad_values = sorted(set(df.loc[unmapped, column].tolist()), key=str)
        raise ValueError(
            f"map_values: column {column!r} has values with no mapping: {bad_values}; "
            "pass on_missing='keep' to leave unmapped values unchanged"
        )
    df[column] = df[column].map(lambda v: mapping.get(v, v))
    return df


_FILTER_OPS: dict[str, Callable[[pd.Series, object], pd.Series]] = {
    "eq": lambda s, v: s == v,
    "ne": lambda s, v: s != v,
    "gt": lambda s, v: s > v,
    "gte": lambda s, v: s >= v,
    "lt": lambda s, v: s < v,
    "lte": lambda s, v: s <= v,
    "isin": lambda s, v: s.isin(v),
    "notna": lambda s, v: s.notna(),
    "isna": lambda s, v: s.isna(),
}


def filter_rows(df: pd.DataFrame, column: str, op: str, value: object = None) -> pd.DataFrame:
    """Keep rows matching a single declared comparison against `column`.

    A closed set of operators, not a free-form predicate: `derive` (not yet
    implemented -- see spec.md §7.3) is where arbitrary expressions belong.

    >>> filter_rows(pd.DataFrame({"x": [1, 2, 3]}), "x", "gt", 1)
       x
    0  2
    1  3
    """
    if op not in _FILTER_OPS:
        raise ValueError(f"filter_rows: unknown op {op!r}; choose from {sorted(_FILTER_OPS)}")
    mask = _FILTER_OPS[op](df[column], value)
    return df[mask].reset_index(drop=True)


def assert_unique(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Validate that `columns` together form a unique key; passes the frame
    through unchanged on success.

    >>> assert_unique(pd.DataFrame({"id": [1, 2]}), ["id"])
       id
    0   1
    1   2
    """
    duplicated = df.duplicated(subset=columns, keep=False)
    if duplicated.any():
        dupes = df.loc[duplicated, columns].drop_duplicates().to_dict(orient="records")
        raise ValueError(
            f"assert_unique: columns {columns} are not unique; duplicate keys: {dupes}"
        )
    return df


def tag_verification(df: pd.DataFrame, value: str, column: str = "_verification") -> pd.DataFrame:
    """Stamp every row with a constant verification-status column.

    >>> tag_verification(pd.DataFrame({"x": [1]}), "TO_VERIFY")
       x _verification
    0  1     TO_VERIFY
    """
    if value not in _VERIFICATION_VALUES:
        raise ValueError(f"tag_verification: {value!r} not in {sorted(_VERIFICATION_VALUES)}")
    df = df.copy()
    df[column] = value
    return df


def derive(df: pd.DataFrame, column: str, expression: str) -> pd.DataFrame:
    """Not implemented this session.

    spec.md §2.4 requires derive to accept a restricted expression grammar
    (parsed, not eval'd). Which grammar -- an ast allowlist vs. a
    hand-rolled parser -- is an open design question (spec.md §7.3), not
    yet decided or recorded in an ADR. Deferred rather than guessed.
    """
    raise NotImplementedError(
        "derive is not implemented yet: its expression grammar is an open "
        "design question (docs/spec.md §7.3), not yet decided. See "
        "docs/adr/ once that decision is made."
    )
