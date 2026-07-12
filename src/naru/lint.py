"""Static artifact linting: `naru lint`, per docs/spec.md §2.4/§2.7 and
docs/adr/0003-transform-loading.md's "Week 6 static linter" callout.

Two artifact kinds, detected by which identity file is present:

- **Pipeline Artifact** (`manifest.yaml`): checks transform.py's import
  and `ops.*` surface via AST -- parsed, never executed. This is what
  closes the one gap ADR-0003 documents and accepts: the restricted
  exec() naru.artifact.load_artifact runs at load time catches a
  module-level `import os`, but an import statement written *inside* the
  `transform` function body only executes -- and so only fails -- when
  that function is later *called* with data. An AST walk sees both,
  without ever running the code. Also checks directory completeness and
  fingerprint parseability.
- **Mapping Artifact** (`mapping.yaml`): checks every column is
  `approved: true` (spec.md §2.7: "before naru lint allows the artifact
  to freeze"), plus completeness for its lighter directory shape
  (naru.mapping's own docstring) and fingerprint parseability.

Every check here enumerates ALL violations in one pass. This is the
opposite of naru.artifact.load_artifact / naru.mapping.
load_mapping_for_execution, which fail loudly on the FIRST problem --
exactly right for actually running something, wrong for a reviewer who
wants the complete list before fixing anything.
"""

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

from naru import ops as ops_module
from naru.artifact import REQUIRED_FILES, ArtifactLoadError, Fingerprint, _load_json_model
from naru.mapping import MappingLoadError, load_mapping, parse_transform_expression


@dataclass
class LintFinding:
    file: Path
    line: int | None
    message: str

    def render(self) -> str:
        location = f"{self.file}:{self.line}" if self.line is not None else str(self.file)
        return f"{location}: {self.message}"


class LintError(Exception):
    """The directory given to naru lint is neither artifact kind -- a
    caller error, not something to list as a finding.
    """


def _public_ops_names() -> set[str]:
    """Every function actually DEFINED in naru.ops (not merely imported
    into its namespace, e.g. `pd`, `re`, `Literal`, `from_excel`) --
    the real op allowlist transform.py's `ops.<name>` calls must stay
    within, per spec.md §2.4.
    """
    return {
        name
        for name, obj in vars(ops_module).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == ops_module.__name__
    }


def _lint_transform_ast(path: Path) -> list[LintFinding]:
    try:
        source = path.read_text()
    except OSError as exc:
        return [LintFinding(path, None, f"could not read file: {exc}")]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [LintFinding(path, exc.lineno, f"syntax error: {exc.msg}")]

    public_ops = _public_ops_names()
    findings: list[LintFinding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = ", ".join(alias.name for alias in node.names)
            findings.append(
                LintFinding(
                    path,
                    node.lineno,
                    f"import {names!r} not allowed -- transform.py may only use the "
                    "injected `ops` module (docs/adr/0003-transform-loading.md)",
                )
            )
        elif isinstance(node, ast.ImportFrom):
            findings.append(
                LintFinding(
                    path,
                    node.lineno,
                    f"'from {node.module} import ...' not allowed -- transform.py may "
                    "only use the injected `ops` module (docs/adr/0003-transform-loading.md)",
                )
            )
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ops"
            and node.attr not in public_ops
        ):
            findings.append(
                LintFinding(
                    path,
                    node.lineno,
                    f"ops.{node.attr} is not a naru.ops function -- allowed: {sorted(public_ops)}",
                )
            )
    return findings


def _lint_fingerprint(path: Path) -> list[LintFinding]:
    """Assumes `path` exists -- callers check existence separately (as
    part of the completeness check) to avoid reporting a missing file
    twice.
    """
    try:
        _load_json_model(path, Fingerprint)
    except ArtifactLoadError as exc:
        return [LintFinding(path, None, str(exc))]
    return []


def _find_source_line(text: str, source_value: str) -> int | None:
    """Best-effort line number for a mapping.yaml column entry, found by
    scanning for its `source:` line. Not real YAML line-tracking (more
    machinery than a human-facing linter needs in v0.1) -- good enough
    since a mapping's source values are effectively unique identifiers
    for its columns already.
    """
    candidates = (
        f'source: "{source_value}"',
        f"source: '{source_value}'",
        f"source: {source_value}",
    )
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip().removeprefix("- ")
        if stripped in candidates:
            return lineno
    return None


def _lint_mapping_approval(path: Path) -> list[LintFinding]:
    try:
        loaded = load_mapping(path)
    except MappingLoadError as exc:
        return [LintFinding(path, None, str(exc))]

    text = path.read_text()
    findings: list[LintFinding] = []
    for column in loaded.columns:
        line = _find_source_line(text, column.source)
        if not column.approved:
            findings.append(
                LintFinding(
                    path, line, f"column {column.source!r} -> {column.target!r} not approved"
                )
            )
        if column.transform:
            try:
                parse_transform_expression(column.transform)
            except MappingLoadError as exc:
                findings.append(LintFinding(path, line, str(exc)))
    return findings


def lint_pipeline_artifact(root: Path) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for filename in REQUIRED_FILES:
        file_path = root / filename
        if not file_path.exists():
            findings.append(LintFinding(file_path, None, "required file missing"))

    golden = root / "golden"
    if not golden.is_dir():
        findings.append(LintFinding(golden, None, "required directory missing"))
    else:
        for filename in ("input_sample.xlsx", "expected_output.parquet"):
            golden_file = golden / filename
            if not golden_file.exists():
                findings.append(LintFinding(golden_file, None, "required file missing"))

    changelog = root / "CHANGELOG.md"
    if changelog.exists() and not changelog.read_text().strip():
        findings.append(LintFinding(changelog, None, "CHANGELOG.md is empty"))

    transform_path = root / "transform.py"
    if transform_path.exists():
        findings.extend(_lint_transform_ast(transform_path))

    fingerprint_path = root / "fingerprint.json"
    if fingerprint_path.exists():
        findings.extend(_lint_fingerprint(fingerprint_path))

    return findings


def lint_mapping_artifact(root: Path) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for filename in ("mapping.yaml", "fingerprint.json", "schema.py"):
        file_path = root / filename
        if not file_path.exists():
            findings.append(LintFinding(file_path, None, "required file missing"))

    mapping_path = root / "mapping.yaml"
    if mapping_path.exists():
        findings.extend(_lint_mapping_approval(mapping_path))

    fingerprint_path = root / "fingerprint.json"
    if fingerprint_path.exists():
        findings.extend(_lint_fingerprint(fingerprint_path))

    return findings


def lint_artifact(root: Path) -> list[LintFinding]:
    """Detect artifact kind and dispatch. `manifest.yaml` present ->
    pipeline artifact; `mapping.yaml` present (no manifest.yaml) ->
    Mapping Artifact (naru.mapping's lighter directory shape, spec.md
    §2.7). Neither present is a caller error, not a lint finding --
    there's nothing here to lint.
    """
    if (root / "manifest.yaml").exists():
        return lint_pipeline_artifact(root)
    if (root / "mapping.yaml").exists():
        return lint_mapping_artifact(root)
    raise LintError(
        f"{root}: neither manifest.yaml nor mapping.yaml found -- not an artifact directory"
    )
