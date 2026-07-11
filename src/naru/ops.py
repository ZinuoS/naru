"""Constrained op API: pure `DataFrame -> DataFrame` transforms.

Per docs/spec.md §2.4, a pipeline artifact's transform.py may compose only
ops from this module. Per docs/adr/0001-lineage-carrier.md, every op that
keeps or drops rows must preserve the row-provenance column (`_src_row` by
default) unchanged; ops that duplicate rows must duplicate it along with
the row.
"""

import pandas as pd

DEFAULT_ROW_MARKER = "_src_row"


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


def drop_blank_rows(df: pd.DataFrame, row_marker: str = DEFAULT_ROW_MARKER) -> pd.DataFrame:
    """Drop rows where every non-marker column is null.

    >>> df = pd.DataFrame({"x": ["a", None], "_src_row": [1, 2]})
    >>> drop_blank_rows(df)
       x  _src_row
    0  a         1
    """
    business_cols = [c for c in df.columns if c != row_marker]
    return df.dropna(subset=business_cols, how="all").reset_index(drop=True)
