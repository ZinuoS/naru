# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-12

<!-- date is a placeholder for the actual v0.1.0 tag date -->

Initial public release: the whole compile-time-intelligence /
run-time-determinism thesis, end to end.

### Added

- Constrained op API (`naru.ops`): pure `DataFrame -> DataFrame`
  transforms (`promote_header`, `drop_empty`, `coerce_numeric`,
  `coerce_date`, `select_sheet`, `unpivot`, `split_column`, `map_values`,
  `filter_rows`, `assert_unique`, `tag_verification`, `derive`) with no
  I/O, network, randomness, or wall-clock access.
- Deterministic profiler (`naru profile`): rule-based header-row
  detection via type-transition scan, per-column type/null-rate/
  cardinality/samples, format-smell detection (percent strings,
  thousands separators, parens-negative, date-serial suspects), and
  cross-sheet duplicate-header detection.
- Pipeline Artifact format and runner (`naru run`): a versioned
  `manifest.yaml`/`fingerprint.json`/`schema.py`/`transform.py`/
  `validations.yaml`/`golden/` directory, with `transform.py` executed
  inside a restricted `exec()` sandbox exposing only `naru.ops` and a
  curated builtins allowlist.
- SQLite storage layer: raw/final/meta zones, row-level lineage
  (`source file hash, sheet, row span -> final row`), and
  append-with-supersede loads (a Type-2-slowly-changing-dimension
  pattern -- point-in-time history comes free).
- Fingerprint and drift detection: a live source file that doesn't match
  the fingerprint a pipeline was compiled against halts with exit code 3
  and a structured `drift_report.json` naming exactly what changed.
- Output-contract validations engine: row-count bounds, key uniqueness,
  null policy, value ranges, sum preservation -- every outcome persisted
  before any pass/fail decision.
- Golden test harness (`naru test`): distinguishes schema drift from
  value drift against a frozen `expected_output.parquet` fixture.
- Mapping Artifact and crosswalk suggestion (`naru map suggest`,
  `naru map learn`): a tiered exact/synonym/profile/llm matching
  cascade (tiers 3-4 shipped as wired stub interfaces, not fabricated
  matches), a persistent, diffable synonym dictionary
  (`~/.naru/synonyms.yaml`), and design-time-only approval gating
  (`approved: true` required before execution).
- Mirror to SQL and Excel targets (`naru mirror`): frozen crosswalk
  application, key-based duplicate detection (existing rows and
  within-batch), a human-readable reconciliation summary, dry-run
  by default, and -- for Excel targets -- append-only writes with a
  pre-write timestamped backup and warehouse-side fingerprint checking
  (drift on either side halts the same way).
- Static artifact linting (`naru lint`): AST-based (never executed)
  checks that `transform.py` only uses the injected `ops` module and
  real `naru.ops` functions, that every mapping line is approved,
  and that an artifact directory is complete.
- Query recipes (`naru query`): `.sql` files with YAML front matter,
  typed and exclusively parameterized param binding (no string
  interpolation into SQL), and result-shape validation against a
  declared column list.
- A full CLI (`naru`, or `python -m naru`), built on typer: `profile`,
  `run`, `test`, `lint`, `map suggest`, `map learn`, `mirror`, `query`,
  with `--help` written for a first-time user and a documented,
  consistent exit-code scheme (`docs/exit_codes.md`).
- Two worked examples: `pipelines/ust_auction_results/v1` (synthetic
  Treasury auction results, demonstrating merged headers and
  high-yield/high-rate column drift across years) and
  `examples/counterparty_mirror` (a synthetic client-statement-to-
  warehouse mirror, including a deliberately drifted column-rename
  variant for exercising the halt).
- Architecture decision records for the three load-bearing design
  choices: row-provenance as an ordinary column, not a wrapper type
  (`0001`); header detection via type-transition scan, not a fixed
  window (`0002`); `transform.py` loaded via restricted `exec()`, not
  plain `importlib` (`0003`).

### Fixed

- Pre-commit's mypy hook now scopes to `src`/`tests`/`scripts`, matching
  the project's own `[tool.mypy]` configuration, instead of picking up
  every tracked `.py` file -- which let two unrelated, same-named
  artifact-owned modules (e.g. two different `schema.py` files under
  separate example directories) collide as a duplicate module name.
