# Exit codes

Every `naru` subcommand uses the same five codes. A code's meaning is the
same everywhere it appears -- there is no per-command reinterpretation.

| Code | Meaning | Example situations |
|---|---|---|
| 0 | Success | The command did what it says on the tin. |
| 1 | Setup/usage error | A file, artifact, mapping, or recipe couldn't be loaded at all: missing file, malformed YAML/JSON, missing required class, unrecognized param. Fix the input or the command line, not the data. Also covers any genuinely unexpected exception -- a real bug, not a modeled failure mode. |
| 2 | Validation / business-rule failure | The command ran correctly and its own logic determined the *data* doesn't pass: `naru test`'s golden mismatch (schema or value drift), `naru run`'s output-contract validation failure, `naru mirror`'s duplicate-key abort or `unmapped_source_columns: fail` abort, `naru query`'s result-shape mismatch. |
| 3 | Fingerprint drift | `naru run` or `naru mirror`: the live file (source side, or -- for an Excel mirror target -- the warehouse side too) doesn't match what the pipeline/mapping was compiled against. Also writes `drift_report.json` in the current directory, designed to be pasted back into a design-time recompilation session (spec.md §2.3). |
| 4 | Lint failure | `naru lint` found one or more violations. Every violation is printed with `file:line` (or just `file` when no line number applies); see `naru.lint.LintFinding`. |

## Why 2 and 4 aren't further split

Earlier in this project, `naru test`'s golden mismatch and `naru run`'s
validation failure used different codes (`2` and `4` respectively), and
lint didn't exist yet. Once `naru lint` needed its own code, `4` was
freed up for it and everything in the same *character* of failure --
"the operation completed, and it correctly told you the data/result
doesn't meet the bar it's held to" -- was consolidated under `2`. A
script driving `naru` in CI only needs to branch on a handful of
outcomes ("did it work," "is the data bad," "did the input drift out
from under it," "is the artifact itself malformed"), not on which
specific subcommand produced the failure.

## Per-command summary

| Command | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| `naru profile` | ok | unreadable file | -- | -- | -- |
| `naru run` | ok | bad artifact | output-contract validation failure | source drift | -- |
| `naru test` | ok | bad artifact | golden (schema/value) drift | -- | -- |
| `naru lint` | ok | not an artifact directory | -- | -- | violations found |
| `naru map suggest` | ok | bad profile/schema file | -- | -- | -- |
| `naru map learn` | ok | bad mapping.yaml | -- | -- | -- |
| `naru mirror` | ok | bad Mapping Artifact, mapped column missing from file | duplicate key, `unmapped_source_columns: fail` | source or warehouse drift | -- |
| `naru query` | ok | recipe not found, malformed recipe | param mismatch, result-shape mismatch | -- | -- |
