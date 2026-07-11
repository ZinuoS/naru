# 0002. Header-row detection: type-transition scan, not fixed-window scoring

## Status

Accepted.

## Context

spec.md §2.2 requires the profiler to detect header row(s) with confidence,
"rule-based: type-transition scan, not ML." Two concrete algorithms were
evaluated against the hostile fixtures h1_merged_headers.xlsx (a two-row
header with merged group labels) and h5_buried_header.xlsx (six banner
rows, header on row 7, footnotes after the data block):

- **A: type-transition scan (unbounded).** Scan rows top-down with no
  fixed window. A row qualifies as a header candidate if it is dense
  (>=50% of columns populated) and mostly text, and the row immediately
  below shows a distinctly different type profile (more numeric/date).
  Confidence scales with how sharp that transition is.
- **B: fixed-window density x uniqueness.** Score every row in the first
  K rows by density times the fraction of distinct string values in that
  row (headers are characteristically all-distinct labels). Pick the
  highest-scoring row in the window; no comparison to neighboring rows.

## Decision

Use Algorithm A (type-transition scan).

On h1, both algorithms correctly identify row 2 as the header (row 1's
33%-dense merged group-label row is below the density gate in both cases).
Neither algorithm resolves h1's multi-row-header structure on its own —
that needs a follow-up step layered on top of whichever core algorithm is
chosen, independent of this decision.

On h5, the two algorithms diverge in a way that matters: B requires a
fixed window K and fails outright (returns a wrong row, not just a low
confidence score) if the true header falls outside it — a real risk given
banner depth varies across real desk files and a small K is a plausible
default. A has no such ceiling; it keeps scanning until it finds a
qualifying transition, so burial depth alone cannot make it miss the
header.

A's known weakness — a weak type-transition signal on sheets where data
columns are mostly categorical/text rather than numeric (row 8 in h5 is
mostly still string-typed) — depresses confidence rather than producing a
wrong answer. That is judged the safer failure mode: a caller that
threshold-checks confidence might unnecessarily distrust a correct
detection, but will not silently load data from the wrong row.

## Consequences

- The density gate (>=50%) and the type-transition comparison are both
  tunable thresholds; they need calibration against more fixtures than
  h1/h5 alone before being trusted on real desk files.
- Confidence scores from A should not be over-interpreted as a strict
  probability — they are stronger evidence of "not a header" (low
  density) than of "this exact row is the header" when the transition
  signal is weak. Downstream code should treat low-but-nonzero confidence
  as "flag for human review," not "reject."
- h1-style multi-row/merged group headers still need a dedicated
  follow-up (e.g. reporting a lower-confidence secondary candidate for the
  row above a strong header hit) — tracked as future work, not solved by
  this ADR.
