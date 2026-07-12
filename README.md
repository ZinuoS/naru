# naru

**naru** (Akkadian: *narû* — an inscribed monument protected by curse
formulae against alteration) turns messy Excel/CSV files into governed
SQLite databases: **compile-time intelligence, run-time determinism.**
The current version of Naru specialises in the financial document pipelines.

[![CI](https://github.com/ZinuoS/naru/actions/workflows/ci.yml/badge.svg)](https://github.com/ZinuoS/naru/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/naru-data)](https://pypi.org/project/naru-data/)
[![Python versions](https://img.shields.io/pypi/pyversions/naru-data)](https://pypi.org/project/naru-data/)
[![License](https://img.shields.io/github/license/ZinuoS/naru)](LICENSE)


<!--
DEMO-GIF-SLOT: record after the Quickstart below is verified working.
  1. asciinema rec naru-demo.cast
  2. Run, in order: the four `naru` commands in "Try it" below, from a
     clean checkout, in a terminal sized to ~100x28.
  3. exit  (stops the recording)
  4. agg naru-demo.cast naru-demo.gif   (https://github.com/asciinema/agg)
  5. Drop naru-demo.gif in this repo's docs/ (or an image host) and
     replace this HTML comment with: ![naru demo](docs/naru-demo.gif)
-->

## Installation

```bash
pip install naru-data
```

The PyPI distribution is `naru-data` (`naru` was already taken) — the
Python import, the CLI command, and everything else stays `naru`:

```bash
naru --help
python -c "import naru; print(naru.__version__)"
```

## Quickstart (from a clone, for trying the worked examples)

```bash
git clone https://github.com/ZinuoS/naru.git
cd naru
uv sync
```

### Try it: the counterparty-mirror demo

`examples/counterparty_mirror` is a real, runnable Mapping Artifact: a
synthetic client statement (column names and units — "Cpn (%)" as a
plain percentage — deliberately different from the warehouse) mirrored
into a formula-laden Excel warehouse workbook, without disturbing any
of its existing formulas, formatting, or other sheets.

`--commit` writes into the warehouse workbook in place (after backing it
up), so work from a throwaway copy rather than the tracked example
directory itself:

```bash
cp -r examples/counterparty_mirror /tmp/counterparty_mirror_demo
DEMO=/tmp/counterparty_mirror_demo

# See what would happen -- writes nothing (dry run is the default)
uv run naru mirror "$DEMO" "$DEMO/client_statement.xlsx"

# Actually write it -- backs up the warehouse file first
uv run naru mirror "$DEMO" "$DEMO/client_statement.xlsx" --commit

# Re-run the same file: a clean, structured failure, not silent duplication
uv run naru mirror "$DEMO" "$DEMO/client_statement.xlsx" --commit

# Same file, one column renamed: halts instead of silently adapting
uv run naru mirror "$DEMO" "$DEMO/client_statement_renamed_column.xlsx"
```

### Try it: the Treasury auction results pipeline

```bash
uv run naru run pipelines/ust_auction_results/v1 \
    pipelines/ust_auction_results/v1/golden/input_sample.xlsx
uv run naru query auction_tail --recipes-dir recipes --param security="10-Year Note"
```

`naru --help`, and `--help` on any subcommand, gets you the rest —
every command's options are documented for someone who has never seen
this repo before.

## What's in v0.1

- A profiler that reads a messy file's structure without touching a
  model: header detection, per-column type/null/cardinality, format
  smells.
- A Pipeline Artifact format and runner, with fingerprint/drift halting,
  an output-contract validation engine, and a golden-fixture test
  harness (`naru test`).
- A Mapping Artifact for client-file-to-warehouse crosswalks
  (`naru map suggest` / `naru map learn`) and a mirror runner
  (`naru mirror`) targeting either a SQLite table or an append-only
  region of an existing Excel workbook.
- Static linting (`naru lint`) and typed, parameterized query recipes
  (`naru query`).
- Full lineage: every row in the target table traces back to
  `(source file SHA-256, sheet, row span, pipeline version, run id)`.

See [docs/spec.md](docs/spec.md) for the full v0.1 specification and
what's deliberately deferred to later versions.

## Mirror-friendly by design

Every runtime dependency (`pandas`, `openpyxl`, `pydantic`, `sqlalchemy`,
`typer`, `pyyaml`, `pyarrow`) has a conda-forge feedstock — no exotic
transitive dependencies to justify to a desk's infra team. Storage is
SQLite: zero-install, single file, and (per the point above) nothing at
runtime ever calls out to a network.

## Learn more

- [docs/spec.md](docs/spec.md) — the full v0.1 specification
- [docs/design.md](docs/design.md) — the architecture, in prose
- [docs/threat_model.md](docs/threat_model.md) — everything naru
  *cannot* do to your data, with each claim tied to the code and test
  enforcing it
- [docs/exit_codes.md](docs/exit_codes.md) — the CLI's exit-code scheme
- [docs/adr/](docs/adr/) — architecture decision records for the
  load-bearing design choices
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to file an issue or send a PR

## License

Apache-2.0. See [LICENSE](LICENSE).
