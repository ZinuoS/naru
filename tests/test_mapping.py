"""Unit tests for src/naru/mapping.py."""

from pathlib import Path

import pandas as pd
import pytest
import yaml

from naru import mapping

VALID_MAPPING_YAML = """\
target: warehouse.positions
key: [deal_id, as_of]
on_duplicate: fail
columns:
  - source: "Cpn (%)"
    target: coupon_rate
    transform: coerce_numeric(scale=0.01)
    basis: synonym
    approved: true
  - source: "Deal Name"
    target: deal_id
    transform: 'map_values(mapping={"Acme Deal": "ACME-1"})'
    basis: llm
    evidence: "client uses marketing names; alias table maintained in-artifact"
    approved: true
unmapped_source_columns: warn
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


class TestColumnMapping:
    def test_valid_exact_basis_needs_no_evidence(self) -> None:
        col = mapping.ColumnMapping(
            source="id", target="id", transform="", basis="exact", approved=True
        )
        assert col.evidence is None

    def test_synonym_basis_needs_no_evidence(self) -> None:
        col = mapping.ColumnMapping(
            source="id", target="id", transform="", basis="synonym", approved=True
        )
        assert col.evidence is None

    def test_profile_basis_without_evidence_raises(self) -> None:
        with pytest.raises(ValueError, match="basis 'profile' requires"):
            mapping.ColumnMapping(source="id", target="id", transform="", basis="profile")

    def test_llm_basis_without_evidence_raises(self) -> None:
        with pytest.raises(ValueError, match="basis 'llm' requires"):
            mapping.ColumnMapping(source="id", target="id", transform="", basis="llm")

    def test_llm_basis_with_empty_string_evidence_raises(self) -> None:
        with pytest.raises(ValueError, match="basis 'llm' requires"):
            mapping.ColumnMapping(source="id", target="id", transform="", basis="llm", evidence="")

    def test_llm_basis_with_evidence_succeeds(self) -> None:
        col = mapping.ColumnMapping(
            source="id", target="id", transform="", basis="llm", evidence="reasoning here"
        )
        assert col.evidence == "reasoning here"

    def test_approved_defaults_to_false(self) -> None:
        col = mapping.ColumnMapping(source="id", target="id", transform="", basis="exact")
        assert col.approved is False


class TestMapping:
    def test_on_duplicate_fail_is_accepted(self) -> None:
        m = mapping.Mapping(
            target="t", key=["id"], on_duplicate="fail", columns=[], unmapped_source_columns="warn"
        )
        assert m.on_duplicate == "fail"

    def test_on_duplicate_skip_is_rejected_with_explanation(self) -> None:
        with pytest.raises(ValueError, match="deliberately unsupported in v0.1"):
            mapping.Mapping(
                target="t",
                key=["id"],
                on_duplicate="skip",
                columns=[],
                unmapped_source_columns="warn",
            )

    def test_on_duplicate_invalid_value_gets_generic_literal_error(self) -> None:
        with pytest.raises(ValueError, match="on_duplicate"):
            mapping.Mapping.model_validate(
                {
                    "target": "t",
                    "key": ["id"],
                    "on_duplicate": "upsert",
                    "columns": [],
                    "unmapped_source_columns": "warn",
                }
            )


class TestParseTransformExpression:
    def test_no_args(self) -> None:
        op_name, kwargs = mapping.parse_transform_expression("coerce_date()")
        assert op_name == "coerce_date"
        assert kwargs == {}

    def test_single_kwarg(self) -> None:
        op_name, kwargs = mapping.parse_transform_expression("coerce_numeric(scale=0.01)")
        assert op_name == "coerce_numeric"
        assert kwargs == {"scale": 0.01}

    def test_multiple_kwargs(self) -> None:
        op_name, kwargs = mapping.parse_transform_expression(
            "coerce_numeric(scale=0.01, allow_null=True)"
        )
        assert kwargs == {"scale": 0.01, "allow_null": True}

    def test_dict_literal_kwarg(self) -> None:
        op_name, kwargs = mapping.parse_transform_expression(
            'map_values(mapping={"A": "Alpha", "B": "Beta"})'
        )
        assert op_name == "map_values"
        assert kwargs == {"mapping": {"A": "Alpha", "B": "Beta"}}

    def test_invalid_syntax_raises(self) -> None:
        with pytest.raises(mapping.MappingLoadError, match="invalid syntax"):
            mapping.parse_transform_expression("coerce_numeric(scale=")

    def test_not_a_call_raises(self) -> None:
        with pytest.raises(mapping.MappingLoadError, match="must be a single function call"):
            mapping.parse_transform_expression("42")

    def test_attribute_call_raises(self) -> None:
        with pytest.raises(mapping.MappingLoadError, match="bare name"):
            mapping.parse_transform_expression("os.system('rm -rf /')")

    def test_disallowed_op_raises(self) -> None:
        with pytest.raises(mapping.MappingLoadError, match="not an allowed transform op"):
            mapping.parse_transform_expression("eval('1')")

    def test_positional_args_raise(self) -> None:
        with pytest.raises(mapping.MappingLoadError, match="positional arguments"):
            mapping.parse_transform_expression("coerce_numeric(0.01)")

    def test_kwargs_expansion_raises(self) -> None:
        with pytest.raises(mapping.MappingLoadError, match=r"\*\*kwargs expansion"):
            mapping.parse_transform_expression("coerce_numeric(**{'scale': 0.01})")

    def test_non_literal_kwarg_value_raises(self) -> None:
        with pytest.raises(mapping.MappingLoadError, match="must be a literal"):
            mapping.parse_transform_expression("coerce_numeric(scale=some_var)")


class TestApplyTransform:
    def test_applies_coerce_numeric_with_scale(self) -> None:
        df = pd.DataFrame({"x": ["2.5"]})
        result = mapping.apply_transform(df, "x", "coerce_numeric(scale=0.01)")
        assert result["x"].iloc[0] == pytest.approx(0.025)

    def test_applies_map_values(self) -> None:
        df = pd.DataFrame({"x": ["A"]})
        result = mapping.apply_transform(df, "x", 'map_values(mapping={"A": "Alpha"})')
        assert result["x"].iloc[0] == "Alpha"


class TestLoadMapping:
    def test_valid_mapping_loads(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mapping.yaml", VALID_MAPPING_YAML)
        m = mapping.load_mapping(path)
        assert m.target == "warehouse.positions"
        assert m.key == ["deal_id", "as_of"]
        assert len(m.columns) == 2

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(mapping.MappingLoadError, match="file not found"):
            mapping.load_mapping(tmp_path / "missing.yaml")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mapping.yaml", "target: [unclosed\n")
        with pytest.raises(mapping.MappingLoadError, match="invalid YAML"):
            mapping.load_mapping(path)

    def test_missing_required_field_names_field(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mapping.yaml", "target: t\nkey: [id]\n")
        with pytest.raises(mapping.MappingLoadError, match="on_duplicate"):
            mapping.load_mapping(path)

    def test_unapproved_column_still_loads_for_review(self, tmp_path: Path) -> None:
        text = VALID_MAPPING_YAML.replace(
            "    transform: coerce_numeric(scale=0.01)\n    basis: synonym\n    approved: true",
            "    transform: coerce_numeric(scale=0.01)\n    basis: synonym\n    approved: false",
        )
        path = _write(tmp_path / "mapping.yaml", text)
        m = mapping.load_mapping(path)
        assert m.columns[0].approved is False

    def test_stub_entry_with_empty_transform_loads_for_review(self, tmp_path: Path) -> None:
        text = """\
target: t
key: [id]
on_duplicate: fail
columns:
  - source: "Some Column"
    target: some_target
    transform: ""
    basis: profile
    evidence: "0.82 profile similarity score"
    approved: false
unmapped_source_columns: warn
"""
        path = _write(tmp_path / "mapping.yaml", text)
        m = mapping.load_mapping(path)
        assert m.columns[0].transform == ""


class TestLoadMappingForExecution:
    def test_fully_approved_mapping_loads(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mapping.yaml", VALID_MAPPING_YAML)
        m = mapping.load_mapping_for_execution(path)
        assert len(m.columns) == 2

    def test_empty_transform_is_a_noop_not_parsed(self, tmp_path: Path) -> None:
        text = """\
target: t
key: [id]
on_duplicate: fail
columns:
  - source: "id"
    target: id
    transform: ""
    basis: exact
    approved: true
unmapped_source_columns: warn
"""
        path = _write(tmp_path / "mapping.yaml", text)
        m = mapping.load_mapping_for_execution(path)
        assert m.columns[0].transform == ""

    def test_unapproved_column_raises_naming_column(self, tmp_path: Path) -> None:
        old = "    target: coupon_rate\n    transform: coerce_numeric(scale=0.01)\n"
        old += "    basis: synonym\n    approved: true"
        new = old.replace("approved: true", "approved: false")
        text = VALID_MAPPING_YAML.replace(old, new)
        path = _write(tmp_path / "mapping.yaml", text)
        with pytest.raises(mapping.MappingLoadError, match=r"not approved.*Cpn \(%\)"):
            mapping.load_mapping_for_execution(path)

    def test_unparseable_transform_on_approved_column_raises(self, tmp_path: Path) -> None:
        text = """\
target: t
key: [id]
on_duplicate: fail
columns:
  - source: "x"
    target: x
    transform: os.system('boom')
    basis: exact
    approved: true
unmapped_source_columns: warn
"""
        path = _write(tmp_path / "mapping.yaml", text)
        with pytest.raises(mapping.MappingLoadError, match="bare name"):
            mapping.load_mapping_for_execution(path)


class TestToYaml:
    def test_round_trips_through_load_mapping(self, tmp_path: Path) -> None:
        original = mapping.load_mapping(_write(tmp_path / "a.yaml", VALID_MAPPING_YAML))
        text = mapping.to_yaml(original)
        path = _write(tmp_path / "b.yaml", text)
        reloaded = mapping.load_mapping(path)
        assert reloaded == original

    def test_omits_null_evidence_for_exact_basis_columns(self, tmp_path: Path) -> None:
        m = mapping.Mapping(
            target="t",
            key=["id"],
            on_duplicate="fail",
            columns=[
                mapping.ColumnMapping(
                    source="id", target="id", transform="", basis="exact", approved=False
                )
            ],
            unmapped_source_columns="warn",
        )
        text = mapping.to_yaml(m)
        parsed = yaml.safe_load(text)
        assert "evidence" not in parsed["columns"][0]


VALID_FINGERPRINT_JSON = """\
{
  "sheet": "Sheet1",
  "header_row": 1,
  "columns": [
    {"name": "id", "type": "integer", "strictness": "strict"}
  ]
}
"""

VALID_TARGET_ROW_SCHEMA = """\
from pydantic import BaseModel


class TargetRow(BaseModel):
    coupon_rate: float
    deal_id: str
"""


def _write_valid_mapping_artifact(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mapping.yaml").write_text(VALID_MAPPING_YAML)
    (root / "fingerprint.json").write_text(VALID_FINGERPRINT_JSON)
    (root / "schema.py").write_text(VALID_TARGET_ROW_SCHEMA)
    return root


class TestLoadMappingArtifact:
    def test_valid_artifact_loads(self, tmp_path: Path) -> None:
        root = _write_valid_mapping_artifact(tmp_path / "mapping_artifact")
        artifact = mapping.load_mapping_artifact(root)
        assert artifact.root == root
        assert artifact.mapping.target == "warehouse.positions"
        assert artifact.fingerprint.sheet == "Sheet1"
        assert artifact.target_row.__name__ == "TargetRow"

    def test_root_not_a_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(mapping.MappingLoadError, match="mapping artifact directory not found"):
            mapping.load_mapping_artifact(tmp_path / "does_not_exist")

    def test_missing_mapping_yaml_raises_naming_file(self, tmp_path: Path) -> None:
        root = _write_valid_mapping_artifact(tmp_path / "mapping_artifact")
        (root / "mapping.yaml").unlink()
        with pytest.raises(mapping.MappingLoadError, match="mapping.yaml: required file missing"):
            mapping.load_mapping_artifact(root)

    def test_missing_fingerprint_raises_naming_file(self, tmp_path: Path) -> None:
        root = _write_valid_mapping_artifact(tmp_path / "mapping_artifact")
        (root / "fingerprint.json").unlink()
        with pytest.raises(
            mapping.MappingLoadError, match="fingerprint.json: required file missing"
        ):
            mapping.load_mapping_artifact(root)

    def test_missing_schema_raises_naming_file(self, tmp_path: Path) -> None:
        root = _write_valid_mapping_artifact(tmp_path / "mapping_artifact")
        (root / "schema.py").unlink()
        with pytest.raises(mapping.MappingLoadError, match="schema.py: required file missing"):
            mapping.load_mapping_artifact(root)

    def test_invalid_fingerprint_json_raises(self, tmp_path: Path) -> None:
        root = _write_valid_mapping_artifact(tmp_path / "mapping_artifact")
        (root / "fingerprint.json").write_text("{not valid json")
        with pytest.raises(mapping.MappingLoadError, match="invalid JSON"):
            mapping.load_mapping_artifact(root)

    def test_schema_missing_target_row_raises(self, tmp_path: Path) -> None:
        root = _write_valid_mapping_artifact(tmp_path / "mapping_artifact")
        (root / "schema.py").write_text("x = 1\n")
        with pytest.raises(mapping.MappingLoadError, match="missing required class 'TargetRow'"):
            mapping.load_mapping_artifact(root)

    def test_schema_target_row_not_basemodel_raises(self, tmp_path: Path) -> None:
        root = _write_valid_mapping_artifact(tmp_path / "mapping_artifact")
        (root / "schema.py").write_text("class TargetRow:\n    pass\n")
        with pytest.raises(
            mapping.MappingLoadError, match="'TargetRow' must be a pydantic BaseModel"
        ):
            mapping.load_mapping_artifact(root)

    def test_schema_import_error_raises(self, tmp_path: Path) -> None:
        root = _write_valid_mapping_artifact(tmp_path / "mapping_artifact")
        (root / "schema.py").write_text("this is not valid python (((\n")
        with pytest.raises(mapping.MappingLoadError, match="failed to import"):
            mapping.load_mapping_artifact(root)
