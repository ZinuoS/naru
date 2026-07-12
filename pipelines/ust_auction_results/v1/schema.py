"""SourceRow/TargetRow contract for ust_auction_results.

SourceRow documents the raw, messy shape of the source columns (after
promote_header positions them, before any coercion) -- design-time
documentation only; nothing enforces it against a live file yet (that's
the fingerprint/drift work, Week 4).

TargetRow is the real contract: runtime.py validates every transformed
row against it before loading. auction_date/issue_date are typed as
`date` even though transform.py hands back ISO-8601 strings -- pydantic
parses ISO date strings into real `date` objects, and store.py always
gets JSON-mode-dumped values back (str) for SQLite, so the round trip is
exact either way.
"""

import datetime as dt

from pydantic import BaseModel


class SourceRow(BaseModel):
    auction_date: str
    security_term: str
    cusip: str
    high_yield: str
    offering_amt: str
    bid_to_cover: float
    issue_date: int


class TargetRow(BaseModel):
    auction_date: dt.date
    security_term: str
    cusip: str
    high_yield: float
    offering_amt: float
    bid_to_cover: float
    issue_date: dt.date
