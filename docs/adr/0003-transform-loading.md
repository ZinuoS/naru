# 0003. transform.py loading: exec() in a restricted namespace

## Status

Accepted.

## Context

spec.md §2.4 says `transform.py` may compose **only** ops from `naru.ops`,
and that a linter (`naru lint`, Week 6) rejects any artifact importing
outside the allowlist. Two ways to load and run `transform.py` were
considered:

- **A: `importlib` module load.** `importlib.util.spec_from_file_location`
  + `module_from_spec` + `exec_module`, then call `module.transform(df)`.
  Standard Python module semantics: real tracebacks, works with
  `inspect`/coverage/debuggers, no surprises.
- **B: `exec()` in a constructed namespace.** Compile the source, then
  `exec()` it into a globals dict that exposes only `naru.ops` and a
  curated builtins allowlist -- no `__import__`, no `open`, no `eval`,
  no network, no wall clock. A disallowed `import` fails immediately with
  a `NameError`, not just a lint warning.

## Decision

Use B (`exec()` in a restricted namespace).

Under A, allowlist enforcement exists *only* in the separate `naru lint`
static check -- which doesn't exist until Week 6. Between now and then
(and for any artifact run without having been linted first: a stale
artifact, a manual `python -m naru.runtime` invocation, a CI
misconfiguration), a module-level `import os` or `import requests` in
`transform.py` would execute without complaint. CLAUDE.md's directive
that hand-written and LLM-assisted transforms stay "equally auditable at
runtime" reads as a runtime property, not just a design-time-linted one --
B gives that for module-level violations today, B and the eventual lint
check become two independent layers rather than one.

## Implementation

`load_artifact` now executes `transform.py`'s module-level code (import
and `def` statements) as part of loading, into a namespace containing only
`{"ops": naru.ops, "__builtins__": <allowlist>}`. It does **not** call
`transform(df)` at load time -- only runtime.py does that, later, with
real data. The builtins allowlist covers common data-manipulation
primitives (`len`, `str`, `int`, `sorted`, standard exception types, etc.)
and deliberately excludes `open`, `eval`, `exec`, `compile`, `__import__`,
`input`, `globals`/`locals`/`vars`.

## Consequences

- **This does not make module-level `pd`/pandas access available inside
  transform.py.** Since the whole point is "compose only from naru.ops,"
  authors cannot write `import pandas as pd` for type hints either --
  `transform.py` functions are written without type annotations that
  reference unavailable names (or with manually-quoted string annotations,
  which Python never evaluates). This is real authoring friction, worth
  weighing against the security benefit.
- **This does not catch every violation at load time.** Module-level
  `import os` is caught immediately (the import statement's bytecode runs
  during the load-time `exec()`). An import statement written *inside*
  the `transform` function body is not caught until that function is
  actually called with data -- function bodies don't execute at
  definition time. This is a real gap for that specific pattern, and is
  exactly the case the eventual Week 6 static linter (AST inspection,
  no execution required) is still needed for -- B is defense in depth,
  not a replacement for lint.
- The restricted-builtins list is a judgment call and will need
  expanding as real pipelines hit `NameError` on legitimate operations
  that aren't actually security-relevant (e.g. if a pipeline genuinely
  needs `format()` or `hasattr()`). Each addition should be a deliberate,
  reviewed change to the allowlist, not a blanket widening.
