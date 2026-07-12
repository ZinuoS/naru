"""Transform for ust_auction_results v1.

Ported from tracer.py's hand-written pipeline (docs/adr/0001,
docs/adr/0002). Composed only from naru.ops, per spec.md §2.4 -- this
file is executed in a restricted namespace exposing only `ops` and a
curated builtins allowlist (docs/adr/0003-transform-loading.md), so it
cannot import anything, including pandas: no type hints reference `pd`.

Header row 3 is not read for its text: one of its cells (F3:G3) is
merged, which makes blindly-promoted header text unreliable -- see
docs/adr/0001-lineage-carrier.md. Column names are hardcoded by position
instead.
"""

HEADER_ROW = 3
COLUMN_NAMES = [
    "auction_date",
    "security_term",
    "cusip",
    "high_yield",
    "offering_amt",
    "bid_to_cover",
    "issue_date",
]


def transform(raw_grid):
    df = ops.promote_header(raw_grid, header_row=HEADER_ROW, column_names=COLUMN_NAMES)
    df = ops.drop_empty(df)
    df = ops.coerce_numeric(df, "offering_amt")
    df = ops.coerce_numeric(df, "high_yield")
    df = ops.coerce_numeric(df, "bid_to_cover")
    df = ops.coerce_date(df, "auction_date", fmt="%m/%d/%Y")
    df = ops.coerce_date(df, "issue_date")
    return df[[*COLUMN_NAMES, "_src_row"]]
