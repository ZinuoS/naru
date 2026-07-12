"""Pipeline Artifact loader and validation.

Per docs/spec.md §2.1: a pipeline artifact is a versioned directory --
manifest.yaml, fingerprint.json, schema.py, transform.py, validations.yaml,
golden/, CHANGELOG.md. `load_artifact` validates the entire directory
eagerly: a malformed artifact fails here, naming the exact file and field,
never later at run time.

fingerprint.json and validations.yaml are schema-validated only this week:
their *content* is checked against the pydantic models below, but nothing
here checks a live source file against the fingerprint or runs the
validations engine against output rows -- both are Week 4 (spec.md §2.3).

transform.py is loaded via exec() into a restricted namespace, not plain
importlib -- see docs/adr/0003-transform-loading.md for why. Its
module-level code (imports, `def` statements) runs at load time, so
module-level violations of the op allowlist fail here; violations written
inside the transform function body are only caught when it's later
called with data (runtime.py), which the ADR documents as a known,
accepted gap pending the Week 6 static linter.
"""

import builtins
import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Literal, TypeVar

import pandas as pd
import yaml
from pydantic import BaseModel, ValidationError

from naru import ops as ops_module

_ModelT = TypeVar("_ModelT", bound=BaseModel)

# Curated allowlist for transform.py's exec() namespace. Deliberately
# excludes open, eval, exec, compile, __import__, input, globals, locals,
# vars -- see docs/adr/0003-transform-loading.md.
_SAFE_BUILTIN_NAMES = (
    "None",
    "True",
    "False",
    "NotImplemented",
    "Ellipsis",
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "float",
    "frozenset",
    "int",
    "isinstance",
    "issubclass",
    "len",
    "list",
    "map",
    "max",
    "min",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "StopIteration",
    "ZeroDivisionError",
    "RuntimeError",
)

REQUIRED_FILES = (
    "manifest.yaml",
    "fingerprint.json",
    "schema.py",
    "transform.py",
    "validations.yaml",
    "CHANGELOG.md",
)


class ArtifactLoadError(Exception):
    """A malformed artifact. The message names the exact file and field."""


class Manifest(BaseModel):
    """Identity, version, and the wiring needed to run this artifact.

    `sheet` and `key` aren't in spec.md's directory-tree comment for
    manifest.yaml verbatim, but the runner (src/naru/runtime.py) needs to
    know which sheet to read and the storage layer (src/naru/store.py)
    needs a natural key to detect what a re-run supersedes -- both have to
    live somewhere, and manifest.yaml is the identity/config file.
    """

    name: str
    version: str
    sheet: str
    target_table: str
    key: list[str]


class HeaderColumnSpec(BaseModel):
    name: str
    type: Literal["string", "integer", "float", "date", "boolean"]
    strictness: Literal["strict", "position_only", "optional"]


class Fingerprint(BaseModel):
    """What the source file must look like. Schema only this week --
    nothing here checks a live file yet (spec.md §2.3, enforcement Week 4).
    """

    sheet: str
    header_row: int
    columns: list[HeaderColumnSpec]
    max_rows_from_header_to_data: int = 5


class RowCountBounds(BaseModel):
    min: int | None = None
    max: int | None = None


class KeyUniqueness(BaseModel):
    columns: list[str]


class NullPolicy(BaseModel):
    column: str
    nulls_allowed: bool


class ValueRange(BaseModel):
    column: str
    min: float | None = None
    max: float | None = None


class Validations(BaseModel):
    """Output contract. Schema only this week -- nothing here runs these
    checks against real output rows yet (engine is Week 4).
    """

    row_count: RowCountBounds = RowCountBounds()
    key_uniqueness: list[KeyUniqueness] = []
    null_policy: list[NullPolicy] = []
    value_ranges: list[ValueRange] = []


class Artifact(BaseModel):
    """A fully loaded, validated pipeline artifact directory."""

    model_config = {"arbitrary_types_allowed": True}

    root: Path
    manifest: Manifest
    fingerprint: Fingerprint
    validations: Validations
    source_row: type[BaseModel]
    target_row: type[BaseModel]
    transform: Callable[[pd.DataFrame], pd.DataFrame]


def _load_yaml_model(path: Path, model: type[_ModelT]) -> _ModelT:
    if not path.exists():
        raise ArtifactLoadError(f"{path}: file not found")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ArtifactLoadError(f"{path}: invalid YAML -- {exc}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ArtifactLoadError(f"{path}: {_format_validation_error(exc)}") from exc


def _load_json_model(path: Path, model: type[_ModelT]) -> _ModelT:
    if not path.exists():
        raise ArtifactLoadError(f"{path}: file not found")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ArtifactLoadError(f"{path}: invalid JSON -- {exc}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ArtifactLoadError(f"{path}: {_format_validation_error(exc)}") from exc


def _format_validation_error(exc: ValidationError) -> str:
    """Render a pydantic ValidationError as 'field.path: message' pairs."""
    parts = []
    for error in exc.errors():
        loc = ".".join(str(p) for p in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)


def _load_python_module(path: Path, module_name: str) -> ModuleType:
    if not path.exists():
        raise ArtifactLoadError(f"{path}: file not found")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ArtifactLoadError(f"{path}: could not create a module spec")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ArtifactLoadError(f"{path}: failed to import -- {exc}") from exc
    return module


def _load_schema(path: Path) -> tuple[type[BaseModel], type[BaseModel]]:
    """Load schema.py and extract its SourceRow/TargetRow pydantic models.

    Loaded via plain importlib, not exec-in-a-restricted-namespace: schema.py
    only declares pydantic models (no naru.ops calls, no I/O), so it carries
    none of the code-execution-safety questions transform.py does -- see
    docs/adr/0003-transform-loading.md for why that one gets a STOP POINT
    and this one doesn't.
    """
    module = _load_python_module(path, "naru_artifact_schema")
    row_types: list[type[BaseModel]] = []
    for name in ("SourceRow", "TargetRow"):
        if not hasattr(module, name):
            raise ArtifactLoadError(f"{path}: missing required class {name!r}")
        cls = getattr(module, name)
        if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            raise ArtifactLoadError(f"{path}: {name!r} must be a pydantic BaseModel subclass")
        row_types.append(cls)
    source_row, target_row = row_types
    return source_row, target_row


def _load_transform(path: Path) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """Load transform.py's `transform` function via exec() into a
    restricted namespace exposing only naru.ops and a curated builtins
    allowlist. See docs/adr/0003-transform-loading.md.

    Only module-level code (imports, `def` statements) runs here -- the
    `transform` function itself is returned unexecuted; runtime.py calls
    it later with real data.
    """
    if not path.exists():
        raise ArtifactLoadError(f"{path}: file not found")
    source = path.read_text()
    try:
        code = compile(source, filename=str(path), mode="exec")
    except SyntaxError as exc:
        raise ArtifactLoadError(f"{path}: syntax error -- {exc}") from exc

    namespace: dict[str, object] = {
        "__name__": "naru_transform",
        "__file__": str(path),
        "__builtins__": {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES},
        "ops": ops_module,
    }
    try:
        exec(code, namespace)  # restricted namespace; see ADR-0003
    except Exception as exc:
        raise ArtifactLoadError(f"{path}: failed to execute -- {exc}") from exc

    if "transform" not in namespace:
        raise ArtifactLoadError(f"{path}: missing required function 'transform'")
    transform_fn = namespace["transform"]
    if not callable(transform_fn):
        raise ArtifactLoadError(f"{path}: 'transform' must be callable")
    return transform_fn


def load_artifact(root: Path) -> Artifact:
    """Load and eagerly validate every file in a pipeline artifact directory.

    Raises ArtifactLoadError immediately on the first problem found, naming
    the exact file and field -- a malformed artifact must never reach run
    time before failing.
    """
    if not root.is_dir():
        raise ArtifactLoadError(f"{root}: artifact directory not found")

    for filename in REQUIRED_FILES:
        if not (root / filename).exists():
            raise ArtifactLoadError(f"{root / filename}: required file missing")
    if not (root / "golden").is_dir():
        raise ArtifactLoadError(f"{root / 'golden'}: required directory missing")
    for filename in ("input_sample.xlsx", "expected_output.parquet"):
        if not (root / "golden" / filename).exists():
            raise ArtifactLoadError(f"{root / 'golden' / filename}: required file missing")

    manifest = _load_yaml_model(root / "manifest.yaml", Manifest)
    fingerprint = _load_json_model(root / "fingerprint.json", Fingerprint)
    validations = _load_yaml_model(root / "validations.yaml", Validations)
    source_row, target_row = _load_schema(root / "schema.py")
    transform = _load_transform(root / "transform.py")

    return Artifact(
        root=root,
        manifest=manifest,
        fingerprint=fingerprint,
        validations=validations,
        source_row=source_row,
        target_row=target_row,
        transform=transform,
    )
