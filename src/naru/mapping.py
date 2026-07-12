"""Mapping Artifact: crosswalk from a client file's columns to a warehouse
schema, per docs/spec.md §2.7.

A Mapping Artifact is a lighter directory shape than a full pipeline
artifact: mapping.yaml (the crosswalk), fingerprint.json (source-side
drift detection, reusing src/naru/fingerprint.py), and schema.py (for
TargetRow, reused for the warehouse table's DDL). It doesn't need
transform.py/validations.yaml/golden/CHANGELOG.md -- those belong to the
full run() pipeline, not to mirroring.

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
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from naru import ops as ops_module
from naru.artifact import _format_validation_error

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


class Mapping(BaseModel):
    target: str
    key: list[str]
    on_duplicate: Literal["fail", "skip"]
    columns: list[ColumnMapping]
    unmapped_source_columns: Literal["warn", "fail"]

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
    transform expression to parse -- run-time mode, what naru mirror uses.
    Raises naming exactly which column(s) aren't ready.
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
        parse_transform_expression(col.transform)
    return mapping


def to_yaml(mapping: Mapping) -> str:
    """Serialize a Mapping to the mapping.yaml format shown in spec.md §2.7."""
    data = mapping.model_dump(mode="json", exclude_none=True)
    text: str = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    return text
