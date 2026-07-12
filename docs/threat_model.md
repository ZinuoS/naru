# Threat model

This is the sales document, and it's written sober: every claim below is
tied to the code that enforces it and the test that would fail if that
code regressed. Where a guarantee has a real boundary or a known gap,
that's stated too — a threat model that only lists strengths isn't one.

Scope: this describes naru **v0.1**, running as a local command-line
tool against files on the machine it's invoked on. It is not a
multi-user service, has no authentication layer, and was never designed
to be one (spec.md §1: "Multi-user, auth, web UI: Never in OSS core").

## What naru cannot do to your data

### No network access at runtime

`transform.py` runs inside a restricted `exec()` namespace exposing
only `naru.ops` and a curated builtins allowlist that excludes
`__import__`, `open`, `eval`, `exec`, and `compile`
(`src/naru/artifact.py::_load_transform`, decided in
[ADR-0003](adr/0003-transform-loading.md)). A module-level `import
requests` fails immediately at load time; grep confirms no module under
`src/naru/` imports any network, socket, or LLM-client library in the
first place — there's nothing to call out to, and no runtime code
path with the means to.

Enforced by: `tests/test_artifact.py::TestLoadArtifact::
test_module_level_import_is_rejected`,
`test_disallowed_builtin_is_rejected`.
Closes a real gap: `tests/test_lint.py::
test_import_buried_in_function_body_is_still_caught` proves `naru lint`
catches an import statement written *inside* the transform function
body too — see "Known gaps" below for why the runtime check alone
can't.

### No LLM call at runtime

Tiers 3 (`profile`) and 4 (`llm`) of `naru map suggest`'s matching
cascade are implemented as wired interfaces that always return an
empty-evidence, no-target proposal — never a real match, and never a
network call to any model provider. Design-time LLM assistance (a human
asking a model to draft a transform or a crosswalk, then reviewing and
freezing the result) happens entirely outside naru's own process, on
the author's machine, before anything is committed. Nothing in the
frozen artifact retains a dependency on that step having happened.

Enforced by: `tests/test_map_suggest.py::TestSuggestTier3And4Stubs`.

### No wall-clock dependence in the data path

Every value written into a target row comes from the source file or an
explicit `as_of` parameter the caller supplies — never from
`datetime.now()`. `store.register_run`'s docstring is explicit that
`as_of` is "stored exactly as given... never backfilled from the wall
clock."

One narrow, deliberate exception: an Excel mirror target's backup file
is named with a timestamp (`naru.mirror._write_backup`). That timestamp
only ever labels a *filename* — it's never read back into a data value,
and it's an explicit, injectable parameter of `mirror()` (`now=`), not
a buried clock read, specifically so it stays testable without mocking
the system clock. This is a bookkeeping label, not a runtime decision.

### Fail loudly on drift, never adapt silently

A live source file that doesn't match the fingerprint a pipeline was
compiled against halts with exit code 3 and a `drift_report.json`
naming exactly what changed — a renamed column, a shifted header, an
inserted sheet — instead of guessing and loading whatever's there. For
an Excel mirror target, the warehouse side gets the identical check
against its own declared header region.

Enforced by: `tests/test_drift.py` (mutation tests against a real
pipeline artifact: renamed column, inserted sheet, shifted header, each
asserted to produce the *specific* named difference, not just any
failure), `tests/test_mirror.py::TestMirrorExcelWarehouseFingerprintDrift`.

### No writes outside a declared target

An Excel mirror target only ever writes new rows into the declared
`column_order` region, strictly below the region's existing last data
row. No pre-existing cell, formula, number format, conditional
formatting rule, named range, or other sheet is ever touched — verified
by reading every one of them back after a real commit-mode mirror, not
by inspecting the writer's code and hoping.

Enforced by: `tests/test_mirror.py::TestMirrorExcelCommit::
test_preserves_every_pre_existing_cell_value_formula_and_number_format`
(the read-back test), `test_appends_new_rows_at_correct_position_only_in_region`,
`test_stray_value_outside_region_in_a_trailing_row_does_not_fool_append_position`.

A timestamped backup of the whole workbook is written before any of
this happens (`test_creates_timestamped_backup_matching_pristine_file`).

### No silent duplication

A row whose natural key already exists in the target — as an active row
already there, or as a duplicate within the same incoming batch — aborts
the *entire* mirror run before anything is written, naming every
colliding key. `on_duplicate` only ever accepts `fail` in v0.1;
`naru.mapping.Mapping` rejects `skip` outright with an explanation, so
there is no silent-upsert code path to accidentally reach.

Enforced by: `tests/test_mirror.py::TestMirrorDuplicateKey`,
`tests/test_mapping.py::TestMapping::test_on_duplicate_skip_is_rejected_with_explanation`.

### No SQL injection via query recipes

`naru query` binds every parameter through SQLite's native named-
placeholder style (`cur.execute(sql, {"name": value})`) exclusively.
There is no string formatting or interpolation into SQL text anywhere
in `src/naru/query.py`.

Enforced by: `tests/test_query.py::TestRunRecipe::
test_param_value_is_never_interpreted_as_sql` — passes a
`'; DROP TABLE t; --`-shaped string as a param value and asserts the
target table is untouched and the string comes back as literal data.

### No arbitrary code in a mapping's column transforms

A `mapping.yaml` column's `transform:` string (e.g.
`coerce_numeric(scale=0.01)`) is parsed with Python's `ast` module and
restricted to a single call on a bare name from a fixed allowlist, with
keyword-only literal arguments — never `eval()`. The same no-eval
doctrine as `transform.py`'s sandbox, applied to a second surface.

Enforced by: `tests/test_mapping.py::TestParseTransformExpression`
(rejects positional args, attribute/method calls, non-literal argument
values, and any op name outside the allowlist).

### Full, mechanical lineage

Every row in a final or mirrored table traces to
`(source file SHA-256, sheet, source row span, pipeline version,
run id)` via the `meta_lineage` table — not a log line an operator has
to go dig for, a column any query can join against. See
[ADR-0001](adr/0001-lineage-carrier.md) for why this is a plain column,
not a wrapper type.

## Known gaps and residual risk

**An import inside a transform function body isn't caught until that
function is called with real data.** `transform.py`'s module-level code
runs at artifact-load time inside the restricted `exec()` sandbox, so a
top-level `import os` fails immediately. An `import os` written *inside*
`def transform(df): ...`, though, is a statement in a function body —
function bodies don't execute at definition time, only when called — so
the sandbox doesn't see it until runtime actually calls `transform()`
with data. `docs/adr/0003-transform-loading.md` documents this as a
known, accepted gap, closed not by the runtime sandbox but by `naru
lint`'s separate, purely static AST walk, which sees both cases without
ever executing the file. **Run `naru lint` before trusting an artifact**
— the runtime sandbox is defense in depth, not a substitute.

**This does not defend against a malicious artifact author.** naru's
guarantees are about drift, accidental error, and constraining what a
*reviewed and frozen* transform can express — they assume the human who
authored and approved a `transform.py`, `mapping.yaml`, or `.sql`
recipe was acting in good faith. Nothing stops that human from writing
`ops.coerce_numeric` calls that produce wrong-but-plausible numbers, or
from approving a mapping line they didn't actually check. Golden tests
and validations catch some of this class of error by comparing output
against expectations, but naru is not a defense against a compromised
or dishonest reviewer with commit access to the artifact directory.

**`derive`'s expression grammar is not implemented in v0.1.**
`naru.ops.derive` exists as a documented stub (`NotImplementedError`) —
spec.md §2.4 requires a restricted, parsed (not `eval`'d) expression
grammar for it, and which specific grammar to use is an open design
question (spec.md §7.3) not yet decided or recorded in an ADR. It ships
as an honest gap, not a partially-working feature.

**IP hygiene is a development practice, not a code-enforced property.**
spec.md's prime directive 7 ("personal machine, personal time, public or
synthetic data only, zero code or schema derived from any employer
system") describes how this project is built, not something the
software itself can verify about its own history. Stated here for
completeness, not as a runtime guarantee.

**Multi-tenant and network-service deployments are out of scope.** naru
has no authentication, no user isolation, and no concept of a network
listener — it's invoked, not resident (spec.md §1). Running it as a
shared service, exposing it to untrusted network input, or relying on it
for access control was never part of the design and none of the above
guarantees should be assumed to extend there.
