# Design

This is spec.md's sections 0–2 rewritten as prose, for a reader who
hasn't seen the design conversation and just wants to understand how the
pieces fit together. For the literal, versioned specification — the
document changes get measured against — see [spec.md](spec.md).

## The problem this solves

Every desk has the same file. A client, a counterparty, or a vendor
sends an Excel sheet. Someone opens it, eyeballs the columns, and
copy-pastes the relevant bits into whatever spreadsheet or database is
"the real one." It works, right up until it doesn't: a column gets
renamed upstream and nobody notices for two auctions, a formula gets
overwritten by a stray paste, or eighteen months later nobody can say
with confidence which numbers were typed by hand and which came
straight from the source. The tool that would fix this — something
that reads the file, figures out what changed, and loads it — sounds
like a natural fit for an LLM. It's also exactly the kind of tool that
would fail a bank's infosec review the moment "figures out what
changed" turns out to mean "an API call to a third-party model runs
against our data every time we load a file."

naru's answer is to split those two problems apart entirely: use the
LLM once, at design time, to help a human write code — then throw the
LLM away and run that code, deterministically, forever.

## Compile-time intelligence, run-time determinism

Think of naru as having a compiler and a runtime, the same way a
programming language does. The compiler is where the intelligence
lives — profiling a messy file, proposing a schema, drafting a
transform, suggesting a crosswalk between one file's column names and
another's. A human (optionally assisted by an LLM) does this once, on
their own machine, and reviews the result before freezing it. The
runtime is where none of that intelligence is allowed back in: it reads
a frozen artifact, checks the live file still looks like what the
artifact was compiled against, and — if it does — applies exactly the
transform that was reviewed and committed. If the file doesn't match,
the runtime doesn't guess; it stops and says exactly what's different.

This is the whole strategic bet, in one sentence: **the LLM is a
compiler, not an interpreter.** Everything else in this document is
consequences of taking that sentence literally.

## The prime directives

Seven rules, non-negotiable, that every other design decision in this
project has to satisfy:

1. **No LLM anywhere in the runtime path.** An LLM may exist only at
   design time, on the author's own machine, and its output is code a
   human reviews and freezes — never a decision made while the pipeline
   is actually running.
2. **Deterministic execution.** The same input bytes produce the same
   output rows, byte-for-byte, forever. No network access at runtime.
   No wall-clock dependence except an explicit `as_of` parameter the
   caller supplies themselves.
3. **Fail loudly on drift, never adapt silently.** If the source file
   doesn't match the fingerprint the pipeline was compiled against, the
   run halts with a structured drift report. Silent adaptation is the
   cardinal sin here — it's exactly what makes a desk stop trusting
   automation in the first place: a wrong number that *looks* fine is
   worse than an error message.
4. **Provenance is a first-class column, not a log line.** Every output
   value has to be traceable back to a source file hash, sheet, and row
   mechanically — something a query can join against, not something an
   operator has to reconstruct from memory or a log archive.
5. **Pure functions in the library, thin drivers at the edge. Config is
   the single source of truth.** No transformation logic hides in a
   notebook, a CLI flag, or a YAML file that secretly gets `eval`'d —
   YAML declares what should happen, Python (reviewed, frozen Python)
   does it.
6. **Golden tests before any change ships.** A pipeline without a frozen
   expected-output fixture is a draft, not a pipeline you can trust.
7. **IP hygiene.** Personal machine, personal time, public or synthetic
   data only, zero code or schema derived from any employer system —
   a constraint on every commit, not a policy document nobody reads.

## What this looks like in practice

### Design time

A pipeline author runs the **profiler** against a messy file. It never
touches a model — it's rule-based, deterministic interrogation: sheet
dimensions, a merged-cell map, header rows detected by a type-transition
scan (a dense, mostly-text row immediately followed by a row that looks
like data — see [ADR-0002](adr/0002-header-detection.md) for why this
algorithm specifically), per-column type/null-rate/cardinality/samples,
and format smells like `%` strings, thousands separators, or Excel
date-serial numbers hiding inside what looks like an integer column.
That profile — not the raw file — is the evidence a human, or an LLM the
human is directing, reasons over to declare intent: what should the
target schema look like, either declared outright or inferred from "one
clean row" the author hand-writes as an example.

From there, a **compiler** step emits transform code — by hand, or
LLM-assisted, the runtime genuinely cannot tell the difference and isn't
supposed to be able to — composed only from a constrained operation API
(`naru.ops`): pure `DataFrame -> DataFrame` functions like
`promote_header`, `coerce_numeric`, `coerce_date`, `unpivot`,
`map_values`, with no I/O, no network, no randomness, no wall clock.
That constraint is the single most load-bearing design choice in this
project — it's what lets a human reviewer trust generated code in
minutes instead of hours, because there's a small, fixed vocabulary of
things the code could possibly be doing, and it's what makes
hand-written and LLM-assisted transforms indistinguishable, and equally
auditable, once frozen.

A **verifier** step — golden fixtures plus human review — is what
actually freezes the result into a **Pipeline Artifact**: a versioned
directory (`manifest.yaml`, `fingerprint.json`, `schema.py`,
`transform.py`, `validations.yaml`, a `golden/` fixture pair, and a
`CHANGELOG.md`) that's fully readable in a pull request. This directory
*is* the infosec pitch: an auditor can read every line that will ever
execute, diff two versions of it, and re-run the golden tests
themselves. Nothing in it phones home, and nothing in it is a model.

### Run time

Running an artifact against a real file is a fixed sequence, and it's
the same sequence every time, for every artifact:

1. **Fingerprint check.** Does the live file's sheet, header signature,
   and structural shape match what the artifact was compiled against?
   If not, halt with a drift report naming exactly what changed — column
   position, expected vs. found header text, whichever check failed —
   designed to be pasted straight back into a design-time recompilation
   session.
2. **Raw zone.** The file's bytes are hashed and stored, content-
   addressed, before anything else happens.
3. **Apply the frozen transform.** Exactly the reviewed code, nothing
   else.
4. **Output-contract validation.** Row-count bounds, key uniqueness,
   null policy, value ranges, sum preservation — every check's outcome
   is persisted before any pass/fail decision, so the audit trail
   survives even a run that goes on to fail.
5. **Load, with lineage.** Rows land in SQLite with `(source file hash,
   sheet, row span, pipeline version, run id)` attached to every one of
   them. Loads are append-with-supersede, never an in-place update — a
   Type-2-slowly-changing-dimension pattern, so a past point in time
   stays queryable for free.
6. **Query.** Typed, saved, parameterized recipes — the same
   compile-then-freeze pattern applied to reads: a human (or an LLM
   drafting on their behalf) authors a query once; only the reviewed,
   frozen recipe ever executes, bound through real parameter binding,
   never string interpolation.

Two variations on this loop exist for the "client sends a file, it has
to land in our warehouse under our names" workflow (`naru map suggest` /
`naru mirror`, spec.md §2.7): the crosswalk between one file's columns
and another's target schema is proposed with the same tiered,
never-auto-approved cascade, and mirroring into an existing Excel
warehouse file is append-only, backed up before any write, with its own
fingerprint check on the warehouse side.

## Why this is worth the extra ceremony

Every piece of ceremony here — the fingerprint, the golden tests, the
constrained op API, the lineage columns — is buying the same thing:
the ability to hand this pipeline to someone who has never seen it, on
a machine with no internet access, and have them trust it without
having to trust *you*. That's a different design goal than "get this
file loaded fast," and it's the one this project optimizes for.
