# Contributing to naru

Thanks for considering it. This project has a small, deliberately rigid
set of rules — most of the review burden on a PR here is "does this
respect the prime directives," not style nitpicking.

## The rules that actually matter

Read [docs/spec.md](docs/spec.md) §0 (Prime directives) and
[docs/design.md](docs/design.md) before writing code. The short version:

- **Nothing at runtime calls a network, a model, or the wall clock**
  (except an explicit `as_of`/`now` parameter the caller supplies).
  `src/naru/ops.py` functions are pure `DataFrame -> DataFrame`; nothing
  else is allowed to compose a `transform.py`.
- **Drift halts, it never gets guessed at.** If you're tempted to make
  a fingerprint check "smarter" by having it silently tolerate a
  near-match, that's the one thing this project is built to refuse to
  do.
- **Every output row traces back to its source file hash, sheet, and
  row.** If you add a new load path, it needs lineage, not a TODO.
- **A pipeline without a frozen golden fixture is a draft.** Golden
  tests gate CI; `scripts/refreeze.py` refuses to regenerate one unless
  the artifact's `CHANGELOG.md` was also touched in the same change —
  don't route around that gate.
- **No `eval()`, ever, on anything that could be user-authored.**
  `transform.py` runs in a restricted `exec()` namespace
  ([ADR-0003](docs/adr/0003-transform-loading.md)); `mapping.yaml`
  column transforms and `.sql` recipe params are parsed with `ast` or
  bound through real DB-API parameter binding. If you're adding a new
  place where a human writes an expression naru evaluates, it needs the
  same treatment — no exceptions for "it's just internal."

If a change would violate one of these, the fix is almost always to
change the design, not to add an exception.

## Development setup

```bash
git clone https://github.com/ZinuoS/naru-data.git
cd naru-data
uv sync
uv run pre-commit install
```

## Before opening a PR

```bash
uv run pytest --cov          # tests + coverage (this project runs at 100%)
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m naru lint pipelines/ust_auction_results/v1
uv run python -m naru lint examples/counterparty_mirror
```

`pre-commit` runs ruff/mypy on every commit; CI runs all of the above
plus the golden harness and lint against every shipped artifact.

Conventions worth knowing before you're surprised by review comments:

- **100% branch coverage on new modules.** Not a soft target — the
  existing test suite is at 100% and PRs are expected to hold the line.
- **No comments explaining *what* code does** — names should do that.
  A comment earns its place only by explaining a non-obvious *why*: a
  hidden constraint, a workaround for a specific bug, something that
  would genuinely surprise a careful reader.
- **Docstring examples are verified, not aspirational.** If a
  docstring has a `>>>` example, its output was run and pasted, not
  guessed.
- **New ops/design decisions that involve a real trade-off get an ADR**
  in `docs/adr/`, not just a commit message. Look at the existing three
  for the format: Context, Decision, Consequences (including the
  downsides you chose to accept).

## Filing a bug

**Every bug report needs a synthetic or sanitized fixture that
reproduces it** — a `.xlsx`/`.csv` you built by hand or scrubbed of
anything identifying, not a redacted screenshot of a real desk file.
Two reasons, both load-bearing: this project's own IP-hygiene rule
(spec.md §0, prime directive 7) means nothing derived from a real employer's data can
touch this repo, in an issue or otherwise; and a fixture a maintainer
can actually run against is the difference between a bug getting fixed
this week and a bug that sits open because nobody can reproduce it. The
issue template below asks for this directly — please don't skip it.

## Filing a feature request

Check `docs/spec.md` §1's "Is not (deferred, resist scope creep)" table
first — if what you want is already listed there, it's probably
deliberately out of scope for the reason given, not an oversight.

## Code of conduct

Be the kind of reviewer you'd want reviewing your own PR. Nothing more
formal than that in v0.1.
