"""Unit tests for src/naru/map_suggest.py."""

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from naru import map_suggest
from naru.mapping import ColumnMapping, Mapping
from naru.profiler import ColumnProfile, SheetProfile


class TargetRow(BaseModel):
    deal_id: str
    coupon_rate: float
    as_of: str


def _col(position: int, header_text: str | None) -> ColumnProfile:
    return ColumnProfile(
        position=position,
        header_text=header_text,
        inferred_type="string",
        null_rate=0.0,
        cardinality=1,
        samples=["x"],
        smells=[],
    )


def _sheet(columns: list[ColumnProfile]) -> SheetProfile:
    return SheetProfile(
        name="Sheet1",
        dimensions="A1:A1",
        n_rows=1,
        n_cols=len(columns),
        merged_cells=[],
        header_candidates=[],
        columns=columns,
    )


class TestNormalizeColumnName:
    def test_folds_case_whitespace_and_punctuation(self) -> None:
        assert map_suggest.normalize_column_name("  Coupon-Rate!! ") == "coupon rate"

    def test_underscore_and_space_normalize_the_same(self) -> None:
        assert map_suggest.normalize_column_name("Deal ID") == "deal id"
        assert map_suggest.normalize_column_name("deal_id") == "deal id"


class TestSynonymsPath:
    def test_default_path_is_under_home_naru(self) -> None:
        assert map_suggest.default_synonyms_path() == Path.home() / ".naru" / "synonyms.yaml"

    def test_env_var_overrides_default_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NARU_SYNONYMS_PATH", "/tmp/custom/synonyms.yaml")
        assert map_suggest.default_synonyms_path() == Path("/tmp/custom/synonyms.yaml")


class TestLoadSaveSynonyms:
    def test_load_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        assert map_suggest.load_synonyms(tmp_path / "missing.yaml") == {}

    def test_save_then_load_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        map_suggest.save_synonyms({"cpn": "coupon_rate"}, path)
        assert map_suggest.load_synonyms(path) == {"cpn": "coupon_rate"}

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "synonyms.yaml"
        map_suggest.save_synonyms({"a": "b"}, path)
        assert path.exists()

    def test_saved_file_is_plain_sorted_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        map_suggest.save_synonyms({"z key": "z_target", "a key": "a_target"}, path)
        text = path.read_text()
        assert text.index("a key") < text.index("z key")
        assert yaml.safe_load(text) == {"z key": "z_target", "a key": "a_target"}


class TestSuggestTier3And4Stubs:
    def test_tier3_always_returns_no_target_and_empty_evidence(self) -> None:
        result = map_suggest.suggest_tier3_profile_similarity(_col(1, "Notes"), TargetRow)
        assert result.source == "Notes"
        assert result.target is None
        assert result.basis == "profile"
        assert result.evidence == ""
        assert result.confidence is None

    def test_tier4_always_returns_no_target_and_empty_evidence(self) -> None:
        result = map_suggest.suggest_tier4_llm(_col(1, "Notes"), TargetRow)
        assert result.source == "Notes"
        assert result.target is None
        assert result.basis == "llm"
        assert result.evidence == ""


class TestSuggest:
    def test_exact_tier_matches_normalized_field_names(self, tmp_path: Path) -> None:
        sheet = _sheet([_col(1, "Deal ID"), _col(2, "As Of")])
        mapping, proposals = map_suggest.suggest(
            sheet,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id", "as_of"],
            synonyms_path=tmp_path / "synonyms.yaml",
        )
        by_source = {c.source: c for c in mapping.columns}
        assert by_source["Deal ID"].target == "deal_id"
        assert by_source["Deal ID"].basis == "exact"
        assert by_source["Deal ID"].approved is False
        assert by_source["As Of"].target == "as_of"
        assert proposals == []

    def test_synonym_tier_matches_via_dictionary(self, tmp_path: Path) -> None:
        synonyms_path = tmp_path / "synonyms.yaml"
        map_suggest.save_synonyms({"cpn": "coupon_rate"}, synonyms_path)
        sheet = _sheet([_col(1, "Cpn (%)")])
        mapping, proposals = map_suggest.suggest(
            sheet,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id"],
            synonyms_path=synonyms_path,
        )
        assert len(mapping.columns) == 1
        assert mapping.columns[0].target == "coupon_rate"
        assert mapping.columns[0].basis == "synonym"
        assert proposals == []

    def test_unmatched_column_produces_tier3_and_tier4_proposals_not_a_mapping_line(
        self, tmp_path: Path
    ) -> None:
        sheet = _sheet([_col(1, "Totally Unrelated Column")])
        mapping, proposals = map_suggest.suggest(
            sheet,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id"],
            synonyms_path=tmp_path / "synonyms.yaml",
        )
        assert mapping.columns == []
        assert [p.basis for p in proposals] == ["profile", "llm"]
        assert all(p.target is None and p.evidence == "" for p in proposals)

    def test_column_with_no_header_text_is_skipped_entirely(self, tmp_path: Path) -> None:
        sheet = _sheet([_col(1, None)])
        mapping, proposals = map_suggest.suggest(
            sheet,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id"],
            synonyms_path=tmp_path / "synonyms.yaml",
        )
        assert mapping.columns == []
        assert proposals == []

    def test_each_target_field_is_claimed_at_most_once(self, tmp_path: Path) -> None:
        # Two source columns that both normalize to "deal id" -- only the
        # first should claim deal_id; the second stays unmatched rather
        # than double-mapping the same target.
        sheet = _sheet([_col(1, "Deal ID"), _col(2, "deal_id")])
        mapping, proposals = map_suggest.suggest(
            sheet,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id"],
            synonyms_path=tmp_path / "synonyms.yaml",
        )
        assert len(mapping.columns) == 1
        assert mapping.columns[0].source == "Deal ID"
        assert [p.source for p in proposals] == ["deal_id", "deal_id"]

    def test_unmapped_source_columns_policy_passes_through(self, tmp_path: Path) -> None:
        sheet = _sheet([])
        mapping, _ = map_suggest.suggest(
            sheet,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id"],
            unmapped_source_columns="fail",
            synonyms_path=tmp_path / "synonyms.yaml",
        )
        assert mapping.unmapped_source_columns == "fail"

    def test_on_duplicate_is_always_fail(self, tmp_path: Path) -> None:
        sheet = _sheet([])
        mapping, _ = map_suggest.suggest(
            sheet,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id"],
            synonyms_path=tmp_path / "synonyms.yaml",
        )
        assert mapping.on_duplicate == "fail"


class TestMapLearn:
    def test_promotes_approved_synonym_match(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        sheet = _sheet([_col(1, "Cpn (%)")])
        mapping = Mapping(
            target="t",
            key=["deal_id"],
            on_duplicate="fail",
            columns=[
                ColumnMapping(
                    source="Cpn (%)",
                    target="coupon_rate",
                    transform="",
                    basis="synonym",
                    approved=True,
                )
            ],
            unmapped_source_columns="warn",
        )
        del sheet  # not needed once the ColumnMapping is hand-built
        report = map_suggest.map_learn(mapping, synonyms_path=path)
        assert report.added == {"cpn": "coupon_rate"}
        assert report.skipped_conflicts == {}
        assert map_suggest.load_synonyms(path) == {"cpn": "coupon_rate"}

    def test_promotes_approved_llm_match(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        mapping = Mapping(
            target="t",
            key=["deal_id"],
            on_duplicate="fail",
            columns=[
                ColumnMapping(
                    source="Deal Name",
                    target="deal_id",
                    transform="",
                    basis="llm",
                    evidence="marketing name alias",
                    approved=True,
                )
            ],
            unmapped_source_columns="warn",
        )
        report = map_suggest.map_learn(mapping, synonyms_path=path)
        assert report.added == {"deal name": "deal_id"}

    def test_skips_unapproved_match(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        mapping = Mapping(
            target="t",
            key=["deal_id"],
            on_duplicate="fail",
            columns=[
                ColumnMapping(
                    source="Cpn (%)",
                    target="coupon_rate",
                    transform="",
                    basis="synonym",
                    approved=False,
                )
            ],
            unmapped_source_columns="warn",
        )
        report = map_suggest.map_learn(mapping, synonyms_path=path)
        assert report.added == {}
        assert not path.exists()

    def test_skips_exact_basis_match(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        mapping = Mapping(
            target="t",
            key=["deal_id"],
            on_duplicate="fail",
            columns=[
                ColumnMapping(
                    source="deal_id", target="deal_id", transform="", basis="exact", approved=True
                )
            ],
            unmapped_source_columns="warn",
        )
        report = map_suggest.map_learn(mapping, synonyms_path=path)
        assert report.added == {}

    def test_relearning_the_same_match_is_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        mapping = Mapping(
            target="t",
            key=["deal_id"],
            on_duplicate="fail",
            columns=[
                ColumnMapping(
                    source="Cpn (%)",
                    target="coupon_rate",
                    transform="",
                    basis="synonym",
                    approved=True,
                )
            ],
            unmapped_source_columns="warn",
        )
        map_suggest.map_learn(mapping, synonyms_path=path)
        second_report = map_suggest.map_learn(mapping, synonyms_path=path)
        assert second_report.added == {}
        assert second_report.skipped_conflicts == {}
        assert map_suggest.load_synonyms(path) == {"cpn": "coupon_rate"}

    def test_conflicting_entry_is_skipped_without_force(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        map_suggest.save_synonyms({"cpn": "old_target"}, path)
        mapping = Mapping(
            target="t",
            key=["deal_id"],
            on_duplicate="fail",
            columns=[
                ColumnMapping(
                    source="Cpn (%)",
                    target="coupon_rate",
                    transform="",
                    basis="synonym",
                    approved=True,
                )
            ],
            unmapped_source_columns="warn",
        )
        report = map_suggest.map_learn(mapping, synonyms_path=path)
        assert report.added == {}
        assert report.skipped_conflicts == {"cpn": "old_target"}
        assert map_suggest.load_synonyms(path) == {"cpn": "old_target"}

    def test_conflicting_entry_is_overwritten_with_force(self, tmp_path: Path) -> None:
        path = tmp_path / "synonyms.yaml"
        map_suggest.save_synonyms({"cpn": "old_target"}, path)
        mapping = Mapping(
            target="t",
            key=["deal_id"],
            on_duplicate="fail",
            columns=[
                ColumnMapping(
                    source="Cpn (%)",
                    target="coupon_rate",
                    transform="",
                    basis="synonym",
                    approved=True,
                )
            ],
            unmapped_source_columns="warn",
        )
        report = map_suggest.map_learn(mapping, synonyms_path=path, force=True)
        assert report.added == {"cpn": "coupon_rate"}
        assert report.skipped_conflicts == {}
        assert map_suggest.load_synonyms(path) == {"cpn": "coupon_rate"}
