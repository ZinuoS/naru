"""Mapping Artifact: crosswalk from a client file's columns to a warehouse
schema, per docs/spec.md §2.7.

A Mapping Artifact is a lighter directory shape than a full pipeline
artifact: mapping.yaml (the crosswalk), fingerprint.json (source-side
drift detection, reusing src/naru/fingerprint.py), and schema.py (for
TargetRow, reused for the warehouse table's DDL). It doesn't need
transform.py/validations.yaml/golden/CHANGELOG.md -- those belong to the
full run() pipeline, not to mirroring.

When `mapping.excel_target` is set, a fourth file is required:
warehouse_fingerprint.json -- the exact same Fingerprint model and
check_fingerprint() function used for source-side drift detection,
reused verbatim for the warehouse side (spec.md §2.7: "fingerprint check
on both sides... each halt with a report"). Its `sheet`/`header_row`
double as the declared data region's own sheet/header row -- ExcelTarget
only adds what Fingerprint doesn't already cover: which physical file,
the region's column-letter boundaries, and which target field lives in
each column position (needed to know where to write new rows; unrelated
to what the drift check reads).

Two load modes, matching spec.md §2.7 ("every mapping line requires
approved: true... before naru lint allows the artifact to freeze"):
`load_mapping` always succeeds and is for design-time review (map
suggest/learn, human editing of still-incomplete drafts); `load_mapping_
for_execution` additionally requires every column be approved and every
transform expression to parse, and is what naru mirror actually runs.

Each column's `transform` is a string like "coerce_numeric(scale=0.01)",
parsed with `ast` -- never `eval()` -- restricted to a small allowlist of
column-level ops with keyword-only literal arguments. Same no-eval
doctrine as transform.py's exec sandbox; see
docs/adr/0003-transform-loading.md. Note: spec.md's own mapping.yaml
example uses `map_values(table=deal_aliases)`, referencing an external,
in-artifact alias table by bare name. That file-reference mechanism is
not implemented this session -- map_values is supported here with an
inline literal dict argument instead (`mapping={...}`, matching
naru.ops.map_values's real parameter name, not spec's illustrative
`table`), which is expressible as an ast literal without needing a new
file convention.
"""

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from openpyxl.utils import column_index_from_string
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from naru import ops as ops_module
from naru.artifact import (
    ArtifactLoadError,
    Fingerprint,
    _format_validation_error,
    _load_json_model,
    _load_python_module,
)

ALLOWED_TRANSFORM_OPS = ("coerce_numeric", "coerce_date", "map_values")


class MappingLoadError(Exception):
    """A malformed mapping.yaml, or one not ready for execution. The
    message names the exact field/column at fault.
    """


class ColumnMapping(BaseModel):
    source: str
    target: str
    transform: str
    basis: Literal["exact", "synonym", "profile", "llm"]
    evidence: str | None = None
    approved: bool = False

    @model_validator(mode="after")
    def _evidence_required_for_profile_and_llm(self) -> "ColumnMapping":
        if self.basis in ("profile", "llm") and not self.evidence:
            raise ValueError(
                f"column {self.source!r}: basis {self.basis!r} requires a "
                "non-empty 'evidence' note explaining why this match was proposed"
            )
        return self


class ExcelTarget(BaseModel):
    """Declares an append-only data region for an Excel mirror target.
    `sheet` and `header_row` are NOT here -- they live in
    warehouse_fingerprint.json (the same Fingerprint model source-side
    drift detection uses), so the region's location is declared exactly
    once. `column_order` gives the target field name at each position
    from `first_data_col` to `last_data_col`, left to right -- this is
    how naru.mirror knows which column to write each mapped value into.

    `first_data_col` must be `"A"` in v0.1 -- see
    docs/adr/0004-excel-mirror-region-offset.md for why (short version:
    naru.fingerprint.check_fingerprint assumes a header signature's
    columns start at position 1, and extending it to check an arbitrary
    offset region is deferred rather than complicating that shared,
    general-purpose machinery mid-feature). A warehouse sheet with real,
    non-region columns to the right of the mirrored region is fully
    supported (last_data_col need not reach the sheet's edge) -- only
    columns to the *left* of the region are the v0.1 restriction.
    """

    path: str
    first_data_col: str
    last_data_col: str
    column_order: list[str]

    @field_validator("first_data_col", "last_data_col")
    @classmethod
    def _valid_column_letter(cls, value: str) -> str:
        try:
            column_index_from_string(value)
        except ValueError as exc:
            raise ValueError(f"{value!r} is not a valid Excel column letter") from exc
        return value

    @field_validator("first_data_col")
    @classmethod
    def _must_start_at_column_a(cls, value: str) -> str:
        if value != "A":
            raise ValueError(
                "first_data_col must be 'A' in v0.1 -- see "
                "docs/adr/0004-excel-mirror-region-offset.md"
            )
        return value

    @model_validator(mode="after")
    def _region_width_matches_column_order(self) -> "ExcelTarget":
        # first_data_col is always "A" (see _must_start_at_column_a), so
        # first <= last always holds for any valid last_data_col -- no
        # separate "first before last" check is reachable to write.
        first = column_index_from_string(self.first_data_col)
        last = column_index_from_string(self.last_data_col)
        width = last - first + 1
        if len(self.column_order) != width:
            raise ValueError(
                f"column_order has {len(self.column_order)} entries but the region "
                f"{self.first_data_col}:{self.last_data_col} is {width} column(s) wide"
            )
        return self


class Mapping(BaseModel):
    target: str
    key: list[str]
    on_duplicate: Literal["fail", "skip"]
    columns: list[ColumnMapping]
    unmapped_source_columns: Literal["warn", "fail"]
    excel_target: ExcelTarget | None = None

    @field_validator("on_duplicate")
    @classmethod
    def _reject_skip(cls, value: str) -> str:
        if value == "skip":
            raise ValueError(
                "on_duplicate: 'skip' is deliberately unsupported in v0.1 "
                "(upsert deferred, spec.md §2.7) -- use 'fail'."
            )
        return value


def parse_transform_expression(expr: str) -> tuple[str, dict[str, object]]:
    """Parse "op_name(kwarg=literal, ...)" into (op_name, kwargs) via ast --
    never eval(). Restricted to ALLOWED_TRANSFORM_OPS, keyword-only
    arguments, literal values only (numbers/strings/booleans/None/list/dict).

    >>> parse_transform_expression("coerce_numeric(scale=0.01)")
    ('coerce_numeric', {'scale': 0.01})
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise MappingLoadError(f"transform {expr!r}: invalid syntax -- {exc}") from exc

    call = tree.body
    if not isinstance(call, ast.Call):
        raise MappingLoadError(f"transform {expr!r}: must be a single function call")
    if not isinstance(call.func, ast.Name):
        raise MappingLoadError(
            f"transform {expr!r}: function must be a bare name, not an attribute "
            "or other expression"
        )
    op_name = call.func.id
    if op_name not in ALLOWED_TRANSFORM_OPS:
        raise MappingLoadError(
            f"transform {expr!r}: {op_name!r} is not an allowed transform op -- "
            f"choose from {sorted(ALLOWED_TRANSFORM_OPS)}"
        )
    if call.args:
        raise MappingLoadError(
            f"transform {expr!r}: positional arguments aren't allowed -- use keyword arguments"
        )

    kwargs: dict[str, object] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise MappingLoadError(f"transform {expr!r}: **kwargs expansion isn't allowed")
        try:
            kwargs[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError) as exc:
            raise MappingLoadError(
                f"transform {expr!r}: argument {kw.arg!r} must be a literal "
                f"(number, string, bool, None, list, or dict) -- {exc}"
            ) from exc
    return op_name, kwargs


def apply_transform(df: pd.DataFrame, column: str, expr: str) -> pd.DataFrame:
    """Apply a parsed transform expression to `column` by calling the real
    naru.ops function -- never eval().
    """
    op_name, kwargs = parse_transform_expression(expr)
    op_fn = getattr(ops_module, op_name)
    result: pd.DataFrame = op_fn(df, column, **kwargs)
    return result


def load_mapping(path: Path) -> Mapping:
    """Load and validate mapping.yaml's structure -- design-time review
    mode. Succeeds for a well-formed, schema-valid file regardless of any
    column's approved status or whether every transform is fully authored
    yet (map suggest's stub entries have an empty transform string until a
    human fills one in).
    """
    if not path.exists():
        raise MappingLoadError(f"{path}: file not found")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise MappingLoadError(f"{path}: invalid YAML -- {exc}") from exc
    try:
        return Mapping.model_validate(raw)
    except ValidationError as exc:
        raise MappingLoadError(f"{path}: {_format_validation_error(exc)}") from exc


def load_mapping_for_execution(path: Path) -> Mapping:
    """Load mapping.yaml and require every column be approved and every
    non-empty transform expression to parse -- run-time mode, what naru
    mirror uses. Raises naming exactly which column(s) aren't ready. An
    empty transform string is a legitimate no-op (a column that needs no
    conversion, e.g. a plain text field mapped straight through) and is
    never parsed -- see apply_transform, which skips calling anything for
    one too.
    """
    mapping = load_mapping(path)
    unapproved = [c.source for c in mapping.columns if not c.approved]
    if unapproved:
        raise MappingLoadError(
            f"{path}: {len(unapproved)} column(s) not approved for execution: "
            f"{unapproved} -- every mapping line needs approved: true before "
            "naru mirror can run it (spec.md §2.7)"
        )
    for col in mapping.columns:
        if col.transform:
            parse_transform_expression(col.transform)
    return mapping


def to_yaml(mapping: Mapping) -> str:
    """Serialize a Mapping to the mapping.yaml format shown in spec.md §2.7."""
    data = mapping.model_dump(mode="json", exclude_none=True)
    text: str = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    return text


@dataclass
class MappingArtifact:
    """A fully loaded Mapping Artifact directory, ready for naru mirror."""

    root: Path
    mapping: Mapping
    fingerprint: Fingerprint
    target_row: type[BaseModel]
    warehouse_fingerprint: Fingerprint | None = None


def _load_target_row(path: Path) -> type[BaseModel]:
    """Load schema.py and extract its TargetRow model -- unlike a full
    pipeline artifact's schema.py, a Mapping Artifact needs no SourceRow
    (there's no per-row schema conformance check on the client-file side,
    only the fingerprint).
    """
    try:
        module = _load_python_module(path, "naru_mapping_schema")
    except ArtifactLoadError as exc:
        raise MappingLoadError(str(exc)) from exc
    if not hasattr(module, "TargetRow"):
        raise MappingLoadError(f"{path}: missing required class 'TargetRow'")
    cls = module.TargetRow
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        raise MappingLoadError(f"{path}: 'TargetRow' must be a pydantic BaseModel subclass")
    return cls


def _load_fingerprint(path: Path) -> Fingerprint:
    try:
        return _load_json_model(path, Fingerprint)
    except ArtifactLoadError as exc:
        raise MappingLoadError(str(exc)) from exc


def load_mapping_artifact(root: Path) -> MappingArtifact:
    """Load a Mapping Artifact directory (mapping.yaml, fingerprint.json,
    schema.py, and -- when the mapping declares an excel_target --
    warehouse_fingerprint.json too) for execution. Always uses
    load_mapping_for_execution -- mirroring is a runtime operation, so
    every column must already be approved -- see naru.mirror.
    """
    if not root.is_dir():
        raise MappingLoadError(f"{root}: mapping artifact directory not found")
    for filename in ("mapping.yaml", "fingerprint.json", "schema.py"):
        if not (root / filename).exists():
            raise MappingLoadError(f"{root / filename}: required file missing")

    mapping = load_mapping_for_execution(root / "mapping.yaml")
    fingerprint = _load_fingerprint(root / "fingerprint.json")
    target_row = _load_target_row(root / "schema.py")

    warehouse_fingerprint: Fingerprint | None = None
    if mapping.excel_target is not None:
        warehouse_fingerprint_path = root / "warehouse_fingerprint.json"
        if not warehouse_fingerprint_path.exists():
            raise MappingLoadError(
                f"{warehouse_fingerprint_path}: required file missing -- "
                "mapping.yaml declares an excel_target, which needs this file "
                "for warehouse-side drift detection (spec.md §2.7)"
            )
        warehouse_fingerprint = _load_fingerprint(warehouse_fingerprint_path)
        width = len(mapping.excel_target.column_order)
        if len(warehouse_fingerprint.columns) != width:
            raise MappingLoadError(
                f"{warehouse_fingerprint_path}: declares "
                f"{len(warehouse_fingerprint.columns)} column(s), but "
                f"excel_target's region is {width} column(s) wide -- these must match"
            )

    return MappingArtifact(
        root=root,
        mapping=mapping,
        fingerprint=fingerprint,
        target_row=target_row,
        warehouse_fingerprint=warehouse_fingerprint,
    )
