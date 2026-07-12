# 0004. Excel mirror target: declared region must start at column A

## Status

Accepted.

## Context

spec.md §2.7 requires an Excel mirror target to declare a data region
(sheet, header row, first-data-column:last-data-column) and to fingerprint
the warehouse side exactly like the source side — the same
`Fingerprint`/`check_fingerprint` machinery, reused verbatim, per the
project's "compile once, reuse the checking machinery" pattern. Two ways
to make that reuse work were available once `naru.mapping.ExcelTarget`
needed to support a region that doesn't necessarily start at the sheet's
first column:

- **A: teach `check_fingerprint` an offset.** Extend
  `naru.fingerprint._check_header_signature` / `_check_column_types` (and
  the `Fingerprint` model) to accept a starting column position, so a
  region's header signature could be checked starting anywhere in the
  sheet.
- **B: require `first_data_col == "A"` in v0.1.** Keep
  `check_fingerprint` untouched — it already assumes a header signature's
  columns start at position 1, since that's true for every source-side
  fingerprint in the codebase — and add a validator to `ExcelTarget`
  rejecting any other value with a clear message.

## Decision

Use B: `ExcelTarget.first_data_col` must be `"A"`.

`check_fingerprint` is shared, general-purpose machinery used by every
pipeline artifact's source-side drift detection, not something built
for this one feature. Extending its column-position semantics to
support an arbitrary offset mid-feature — while also shipping the
Excel-target writer, the backup mechanism, and the read-back
preservation test in the same session — risked either rushing that
change or leaving it half-verified against the *existing* fingerprint
call sites. B keeps the "reuse Week 4 machinery" claim literally true
(the exact same, unmodified function checks both sides) and turns a
real gap into an explicit, validated, documented constraint instead of
an untested code path.

This is not a fundamental limitation of the design: a warehouse sheet
may still have real, non-mirrored columns to the *right* of the region
(`last_data_col` need not reach the sheet's edge, and the demo fixture
exercises exactly this — see `tests/fixtures/warehouse_workbook.xlsx`'s
`Notional`/`Total` columns). Only columns to the *left* of the region
are the v0.1 restriction.

## Consequences

- A warehouse workbook whose mirrored columns don't happen to start at
  column A needs to be restructured (or the columns reordered) before
  naru can target it — a real, user-facing limitation, not just an
  internal one.
- Lifting this restriction later means extending
  `naru.fingerprint.check_fingerprint` to accept a starting column
  offset and re-verifying every existing source-side fingerprint call
  site still behaves identically at offset zero — tracked as future
  work, not solved by this ADR.
- `naru.mapping.ExcelTarget`'s `_must_start_at_column_a` validator is
  the single enforcement point; removing this ADR's constraint means
  removing that validator *and* doing the `check_fingerprint` extension
  together, not just deleting the check.
