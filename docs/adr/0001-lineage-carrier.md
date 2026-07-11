# 0001. Row-lineage carrier: provenance column, not a wrapper type

## Status

Accepted.

## Context

Every transform in a pipeline artifact must leave each output row mechanically
traceable back to `(source_file_sha256, sheet, source_row_span)` (CLAUDE.md
prime directive 4; spec.md §2.5). Two designs were considered for how a
row's origin travels through a chain of transforms:

- **Wrapper class.** A carrier type (e.g. `TrackedFrame`) bundles a
  `pandas.DataFrame` with a separate `origin` series, and every op becomes
  `TrackedFrame -> TrackedFrame`.
- **Provenance column.** An ordinary `_src_row` column is added immediately
  after ingest and rides through every transform as plain data. Ops stay
  `DataFrame -> DataFrame`.

## Decision

Use the provenance-column approach. A `_src_row` column (the 1-indexed
source row number in the raw sheet) is attached right after the raw grid is
read, and every transform is expected to carry it through like any other
column — including it in `id_vars` for row-duplicating ops such as unpivot.

This is the direct choice because:

- spec.md §2.4 defines `naru.ops` as pure `DataFrame -> DataFrame`
  functions; a wrapper type would break that signature and complicate the
  constrained-op-API/linter story that the whole product is built around.
- CLAUDE.md prime directive 4 asks for provenance to be "first-class... not
  a log line" — a column is the most literal reading of that, and it is
  visible in any dataframe a reviewer or the golden-test diff inspects
  directly, rather than living in a wrapper's internals.

## Consequences

- Nothing structurally prevents a careless transform from dropping or
  corrupting `_src_row` (e.g. a wildcard `.drop(columns=...)`, or a
  groupby/join that doesn't naturally carry it through). This must be
  caught by discipline plus downstream checks (schema/lineage validation
  comparing expected vs. actual `_src_row` coverage), not by the type
  system.
- Row-duplicating ops (unpivot, split) must explicitly include `_src_row`
  in whatever "carry these columns through unchanged" mechanism they use;
  this is a convention to enforce in `naru.ops`, not something automatic.
- If cell-level lineage is added later (spec.md §2.5 lists it as optional
  future work), a single `_src_row` column does not extend cleanly to
  cell granularity — that will need a separate mechanism, evaluated when
  the need is concrete rather than speculated now.
- Ops remain exactly the pure `DataFrame -> DataFrame` shape the spec
  describes, so hand-written and LLM-assisted transforms stay
  indistinguishable and equally auditable at runtime, per spec.md §2.4.
