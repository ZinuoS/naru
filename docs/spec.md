# NARU — v0.1 Specification

**Compile-time intelligence, run-time determinism.**
A framework that turns messy desk files into governed SQL databases, designed to survive bank infosec review because the objectionable parts (LLM calls, nondeterminism, data egress) are architecturally absent at runtime — not merely minimized.

*A narû (Akkadian) is the inscribed monument on which binding records — royal decrees, boundary grants, the Code of Hammurabi — were made permanent, canonical, and publicly auditable, typically closed with curse formulae against anyone who would alter the inscription. That is the product.*

> **Name settled: `naru`.** PyPI availability to be confirmed at claim time; if the bare distribution name is taken, fall back to distribution `naru-data` while keeping `import naru` throughout.

---

## 0. Prime directives (non-negotiable, from doctrine)

1. **No LLM anywhere in the runtime path.** LLMs may exist only at design time, on the author's own machine, and their output is *code that a human reviews and freezes* — never a runtime decision.
2. **Deterministic execution.** Same input bytes → same output rows, byte-for-byte, forever. No network access at runtime. No wall-clock dependence except explicit `as_of` parameters.
3. **Fail loudly on drift, never adapt silently.** If the source file doesn't match the fingerprint the pipeline was compiled against, the run halts with a structured drift report. Silent adaptation is the cardinal sin — it is exactly what makes desks distrust automation.
4. **Provenance is a first-class column, not a log line.** Every output value must be traceable to (source file hash, sheet, cell/row) mechanically, not forensically.
5. **Pure functions in `src/`, thin drivers at the edge. Config is the single source of truth.** No transformation logic in notebooks, CLIs, or YAML — YAML declares, Python transforms.
6. **Golden tests before any change ships.** A pipeline without frozen expected-output fixtures is a draft, not a pipeline.
7. **IP hygiene:** personal machine, personal time, public or synthetic data only, zero code or schema derived from any employer system. This is a constraint on *every* commit, enforced by never having employer material on the dev machine at all.

---

## 1. What v0.1 is — and is not

**Is:** a Python library + CLI that lets one analyst (a) interrogate a messy Excel/CSV file, (b) author — with optional LLM assistance *at design time* — a frozen, reviewable **Pipeline Artifact**, and (c) run that artifact deterministically, forever after, to load a local SQL database with full lineage and validation.

**Is not (deferred, resist scope creep):**

| Deferred | To | Why |
|---|---|---|
| PDF ingestion (readable + OCR) | v0.2 | Different extraction stack; Excel proves the architecture |
| Postgres / warehouse targets | v0.2 | SQLite is zero-install and passes any locked-down machine |
| Natural-language querying | v0.3 | Same compile-don't-chat pattern: NL → *saved, reviewed* query recipe |
| Fuzzy row-level record linkage (no clean key) | v0.2 | Hard problem; ship key-based mirroring first (§2.7) |
| In-place Excel upsert (modify existing rows) | v0.2+ | Append-only mirroring is safe; touching live cells is not |
| Scheduler / daemon / service | v0.3+ | v0.1 is invoked, not resident |
| Multi-user, auth, web UI | Never in OSS core | That's the eventual enterprise layer (the Anaconda move) |

The whole strategic bet lives in one sentence: **the LLM is a compiler, not an interpreter.**

---

## 2. Architecture

```
DESIGN TIME (LLM permitted, author's machine)          RUN TIME (deterministic, air-gap-safe)
┌──────────────────────────────────────────┐          ┌──────────────────────────────────────┐
│ 1. PROFILER   naru profile messy.xlsx   │          │ 5. RUNNER   naru run artifact/ f.xlsx│
│    deterministic interrogation → profile │          │    a. fingerprint check ── drift? ───┼─→ halt +
│                                          │          │    b. raw zone: store bytes + SHA256 │   drift
│ 2. INTENT     target schema, declared    │          │    c. apply frozen transforms        │   report
│    or example-based ("one clean row")    │          │    d. output contract validation     │
│                                          │          │    e. load SQLite + lineage rows     │
│ 3. COMPILER   emits transform code       │          │    f. append run manifest            │
│    against the constrained op API        │          └──────────────────────────────────────┘
│    (LLM-assisted or hand-written —       │
│    runtime cannot tell the difference)   │          ┌──────────────────────────────────────┐
│                                          │          │ 6. QUERY    naru query recipes       │
│ 4. VERIFIER   golden fixtures + human    │          │    typed helpers, saved named queries │
│    review → freeze PIPELINE ARTIFACT     │          └──────────────────────────────────────┘
└──────────────────────────────────────────┘
```

### 2.1 The Pipeline Artifact (the product's atomic unit)

A versioned directory, fully human-readable, reviewable in a PR:

```
pipelines/ust_auction_results/v3/
├── manifest.yaml        # identity, version, source fingerprint spec, target schema ref
├── fingerprint.json     # what the source must look like (§2.3)
├── schema.py            # pydantic models: SourceRow, TargetRow — types are the contract
├── transform.py         # pure functions ONLY, composed from the op API (§2.4)
├── validations.yaml     # output contract: row-count bounds, sum preservation,
│                        #   key uniqueness, null policy, value ranges
├── golden/
│   ├── input_sample.xlsx    # sanitized/synthetic slice of a real messy file
│   └── expected_output.parquet
└── CHANGELOG.md         # why each version exists; drift reports that forced it
```

**The infosec pitch is this directory.** An auditor can read every line that will ever execute, diff versions, and re-run goldens. Nothing here phones home; nothing here is a model.

### 2.2 Profiler (deterministic, no LLM)

`naru profile file.xlsx --out profile.json` emits, per sheet: dimensions; merged-cell map; detected header row(s) with confidence (rule-based: type-transition scan, not ML); per-column inferred type, null rate, cardinality, value samples; unit/format smells (`%` in strings, thousands separators, Excel date-serial suspects, negative-in-parens accounting notation); cross-sheet duplicate-header detection. The profile is the *evidence* the compiler (human or LLM) reasons over — the LLM never touches the raw file directly, only the profile plus small redactable samples the author explicitly approves.

### 2.3 Fingerprint & drift (the trust mechanism)

`fingerprint.json` declares what the pipeline was compiled against: expected sheet names (exact or regex), header signature per sheet (ordered column names + types, with per-column `strict | position_only | optional` flags), and structural invariants (e.g., "data starts within 5 rows of header"). At runtime, mismatch → exit code 3 + `drift_report.json` naming exactly what changed ("sheet 'Results' col 7: expected `High Yield`, found `High Rate`"). The drift report is *designed to be pasted back into design time* — it's the input to recompilation. This closes the loop honestly: format drift is a human-reviewed schema event, never a runtime guess.

### 2.4 Constrained op API (what makes LLM-written code safe to freeze)

`transform.py` may compose **only** ops from `naru.ops` — pure `DataFrame → DataFrame` functions with no I/O, no network, no randomness, no wall clock: `select_sheet, promote_header, drop_empty, coerce_numeric, coerce_date, unpivot, split_column, map_values, derive, filter_rows, assert_unique, tag_verification(...)`. `derive` accepts a restricted expression grammar (parsed, not `eval`'d). A linter (`naru lint`) rejects any artifact importing outside the allowlist. This is the load-bearing design choice: the constrained surface is what lets a reviewer trust generated code in minutes rather than hours, and what makes "LLM-assisted" and "hand-written" indistinguishable — and equally auditable — at runtime.

### 2.5 Storage & lineage

SQLite, staged zones mirrored as schemas: `raw` (immutable: file bytes hash-addressed on disk, `raw.files` registry), `final` (target tables), `meta` (`runs`, `lineage`, `validation_results`). Lineage at row granularity minimum: `(final_table, row_id) → (file_sha256, sheet, source_row_span, pipeline_version, run_id)`; cell granularity optional per column via config. Every target table carries `_verification` (`VERIFIED | TO_VERIFY | DERIVED`) and `_run_id`. Loads are append-with-supersede (point-in-time preserved), never in-place update — the longitudinal record comes free.

### 2.6 Query recipes (v0.1 minimal)

`recipes/*.sql` with YAML front-matter (name, params, expected columns); `naru query <name> --param k=v` executes with typed param binding and validates the result shape. NL-to-recipe generation is v0.3, but the *pattern* is fixed now: language models may author recipes at design time; only reviewed, frozen recipes execute.

### 2.7 Mapping & Mirroring (crosswalks: file A → warehouse B)

The daily desk workflow this serves: client sends a file whose columns are *their* names; rows must land in your warehouse (SQL table **or** an existing Excel warehouse file) under *your* names, converted to *your* units, without anyone hand-copying. Same compiler doctrine: **matching intelligence at design time, frozen crosswalk at run time. "Dynamic" means the mapping is easy to author and re-author — never that it guesses at runtime.**

**Design time — `naru map suggest client_profile.json warehouse_schema.py`** emits a draft crosswalk via a tiered cascade, each tier tagged in the output so the reviewer sees *why* each match was proposed:

1. `exact` — normalized name equality (case/whitespace/punctuation-folded)
2. `synonym` — hit in the shared synonym dictionary (`~/.naru/synonyms.yaml`, see below)
3. `profile` — type + distribution similarity (same dtype, overlapping value ranges, matching null pattern); proposed, never auto-approved
4. `llm` — design-time suggestion from profiles only; proposed, never auto-approved

Every mapping line requires `approved: true` (set by a human) before `naru lint` allows the artifact to freeze. Tiers 3–4 additionally require an `evidence:` note.

**The Mapping Artifact** — added to the pipeline directory:

```
├── mapping.yaml
│     target: warehouse.positions          # SQL table or Excel region ref
│     key: [deal_id, as_of]               # v0.1: exact keys only
│     on_duplicate: fail                   # fail | skip  (upsert deferred)
│     columns:
│       - source: "Cpn (%)"
│         target: coupon_rate
│         transform: coerce_numeric(scale=0.01)   # % → decimal, from the op API
│         basis: synonym
│         approved: true
│       - source: "Deal Name"
│         target: deal_id
│         transform: map_values(table=deal_aliases)
│         basis: llm
│         evidence: "client uses marketing names; alias table maintained in-artifact"
│         approved: true
│     unmapped_source_columns: warn        # warn | fail — silent drops forbidden
```

**Run time — `naru mirror pipelines/client_x/v2 client_file.xlsx --into warehouse.xlsx --dry-run`**:
fingerprint check on **both** sides (client format drift *and* warehouse layout drift each halt with a report) → apply frozen crosswalk + transforms → key-based duplicate check → emit. Excel targets are **append-only into a declared data region**: existing cells, formulas, and formatting are never touched; a timestamped backup of the warehouse file is written first; `--dry-run` (the default) prints the exact row diff and reconciliation summary (row counts in/out, numeric column sums pre/post-conversion, unmapped columns) and requires `--commit` to write. SQL targets go through the standard §2.5 load path and get lineage rows like everything else — a mirrored row is traceable to the client file's hash and row span.

**The synonym dictionary is the flywheel.** Every human-approved `synonym`/`llm` match is offered for promotion into `synonyms.yaml` (`naru map learn`), so crosswalk N+1 starts warmer than crosswalk N. This file is plain YAML, diffable, and shareable across a team — it is the accumulating asset of §2.7, exactly analogous to an alias table in entity resolution, and over time it is worth more than the code.

---

## 3. Worked example shipping with v0.1

**Treasury auction results.** Public data, personally clean (already built independently in a different form — reimplement from scratch here, no code reuse), instantly legible to any rates person, and genuinely messy in the wild: TreasuryDirect exports, dealer-circulated Excel summaries with merged headers, mixed date formats, high-yield vs high-rate column drift across years — a natural drift-report demo. Repo ships: the raw messy sample (synthetic-but-realistic), the full artifact directory, and a 10-minute README walkthrough ending in `naru query auction_tail --param security=10Y`.

A second example (`examples/counterparty_mirror/`) uses two fully synthetic files — a "client statement" and a formula-laden "warehouse workbook" with deliberately different column names and units — to demo the full §2.7 loop: `map suggest` → human approval → `mirror --dry-run` → commit → re-run against a renamed-column variant to show the drift halt. This is the demo that will resonate with anyone who has ever done the copy-paste-and-pray workflow, so it leads the README.

---

## 4. Repo layout & stack

```
naru/
├── src/naru/{profiler, ops, compile, runtime, store, query, cli}.py
├── pipelines/            # example artifacts (§2.1)
├── recipes/
├── tests/                # pytest; golden tests are the spine
├── docs/                 #   quickstart.md, design.md (§0–2 adapted), threat_model.md
├── pyproject.toml        # deps: pandas, openpyxl, pydantic, typer, sqlalchemy — all
│                         #   mirror-friendly, no exotic transitive deps (check each)
└── LICENSE               # Apache-2.0 (patent grant matters if this ever meets enterprises)
```

Python ≥3.11. `threat_model.md` is unusual for a v0.1 and that's the point — it's the document form of the pitch: *here is everything this tool cannot do to your data.*

---

## 5. Seven-week nights-and-weekends plan

*(Was six; §2.7 mapping/mirroring bought a week. If time pressure bites, the Excel mirror target — week 6 — is the cut line, not the crosswalk: SQL-target mirroring already proves the architecture.)*

| Wk | Deliverable | Definition of done |
|---|---|---|
| 1 | Repo, CI, `ops` core (8 ops) + tests | `pip install -e .` green; ops 100% covered |
| 2 | Profiler | Correct profile on 5 deliberately hostile Excel fixtures |
| 3 | Artifact format + runner + SQLite/lineage | Hand-written UST pipeline runs end-to-end; lineage queryable |
| 4 | Fingerprint/drift + validations + golden harness | Mutated input → exit 3 + correct drift report; goldens gate CI |
| 5 | Mapping Artifact: `map suggest` (tiers 1–2 + stub 3–4), frozen crosswalk apply, `mirror --dry-run` to SQL target | Client-file fixture lands in warehouse table with lineage; dup key → clean failure |
| 6 | Excel append-only mirror target + backup/diff, `naru lint`, query recipes | Mirror demo into a formula-laden warehouse workbook leaves existing cells byte-identical |
| 7 | Docs, threat model, second example, **publish** | Public repo; post write-up; send to 5 people who live this pain |

Design-time LLM *assist* tooling (profile-to-draft-transform prompting) is deliberately **week 7+**: the architecture must prove out with a hand-written pipeline first, and the artifact format is identical either way.

---

## 6. Success metrics & kill criteria (pre-registered, per doctrine)

**Proof-of-life (3 months post-publish):** ≥1 stranger opens a real issue about a real file; a colleague-of-a-colleague onboards a new messy source in <45 min without your help; you yourself reach for it by default for the next personal pipeline.

**Signal to invest more:** inbound "can this run on our approved mirror" question from anyone at a fund/bank — that is the entire thesis validating itself.

**Kill / freeze criteria:** if by month 6 the only user is the author, freeze it as a portfolio artifact without guilt — it will have already paid for itself in interviews, and the market will have answered the startup question with data instead of speculation.

---

## 7. Open design questions (decide during week 1–2, log decisions in `docs/adr/`)

1. Polars vs pandas as the internal frame (pandas is mirror-safe everywhere; polars is faster and stricter — lean pandas for v0.1 reach).
2. Row-level vs cell-level lineage default (row default, cell opt-in per column, per §2.5 — confirm cost on a 1M-row load).
3. Expression grammar for `derive`: subset via `ast` allowlist vs a tiny hand-rolled parser (allowlist is faster to ship; parser is safer to freeze — benchmark reviewer legibility, not just security).
4. Whether `fingerprint` strictness tiers need a fourth mode (`advisory`: warn-and-log without halting) for exploratory use — infosec story is cleaner without it; usability may demand it.
