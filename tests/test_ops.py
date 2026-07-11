"""Unit tests for src/naru/ops.py.

Every op that keeps/drops/duplicates rows gets an explicit test that
provenance (`_src_row`) survives correctly, per docs/adr/0001-lineage-carrier.md.
"""

import math

import pandas as pd
import pytest

from naru import ops


class TestPromoteHeader:
    def test_drops_rows_at_or_above_header_and_renames_by_position(self) -> None:
        df = pd.DataFrame(
            {
                0: ["banner", "header", "a", "c"],
                1: [None, "header2", "b", "d"],
                "_src_row": [1, 2, 3, 4],
            }
        )
        result = ops.promote_header(df, header_row=2, column_names=["x", "y"])
        expected = pd.DataFrame({"x": ["a", "c"], "y": ["b", "d"], "_src_row": [3, 4]})
        pd.testing.assert_frame_equal(result, expected)

    def test_wrong_column_name_count_raises(self) -> None:
        df = pd.DataFrame({0: ["a"], 1: ["b"], "_src_row": [1]})
        with pytest.raises(ValueError, match="expected 2 column names"):
            ops.promote_header(df, header_row=0, column_names=["only_one"])

    def test_custom_row_marker(self) -> None:
        df = pd.DataFrame({0: ["header", "a"], "row_num": [1, 2]})
        result = ops.promote_header(df, header_row=1, column_names=["x"], row_marker="row_num")
        expected = pd.DataFrame({"x": ["a"], "row_num": [2]})
        pd.testing.assert_frame_equal(result, expected)


class TestDropEmpty:
    def test_drops_rows_where_every_business_column_is_null(self) -> None:
        df = pd.DataFrame(
            {
                "x": ["a", None, "c"],
                "y": ["b", None, "d"],
                "_src_row": [1, 2, 3],
            }
        )
        result = ops.drop_empty(df)
        expected = pd.DataFrame({"x": ["a", "c"], "y": ["b", "d"], "_src_row": [1, 3]})
        pd.testing.assert_frame_equal(result, expected)

    def test_keeps_rows_with_at_least_one_non_null_business_value(self) -> None:
        df = pd.DataFrame({"x": ["a", None], "y": [None, None], "_src_row": [1, 2]})
        result = ops.drop_empty(df)
        expected = pd.DataFrame({"x": ["a"], "y": [None], "_src_row": [1]})
        pd.testing.assert_frame_equal(result, expected)

    def test_ignores_row_marker_column_when_checking_blankness(self) -> None:
        df = pd.DataFrame({"x": [None], "_src_row": [1]})
        result = ops.drop_empty(df)
        assert result.empty


class TestCoerceNumeric:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("38,000", 38000.0),
            ("1,234.5", 1234.5),
            ("500", 500.0),
            ("2.747%", 0.02747),
            ("0%", 0.0),
            ("(1,234.5)", -1234.5),
            ("(500)", -500.0),
            ("  42  ", 42.0),
            ("\t7.5\n", 7.5),
            ("(2.747%)", -0.02747),
        ],
    )
    def test_parses_messy_numeric_formats(self, raw: str, expected: float) -> None:
        result = ops.coerce_numeric(pd.DataFrame({"x": [raw]}), "x")
        assert result["x"].iloc[0] == pytest.approx(expected)
        assert result["x"].dtype == "float64"

    def test_passthrough_for_already_numeric_values(self) -> None:
        df = pd.DataFrame({"x": pd.Series([2.5, 3], dtype="object")})
        result = ops.coerce_numeric(df, "x")
        assert result["x"].tolist() == [2.5, 3.0]

    def test_empty_string_raises_by_default(self) -> None:
        with pytest.raises(ValueError, match="unparseable value"):
            ops.coerce_numeric(pd.DataFrame({"x": [""]}), "x")

    def test_whitespace_only_string_raises_by_default(self) -> None:
        with pytest.raises(ValueError, match="unparseable value"):
            ops.coerce_numeric(pd.DataFrame({"x": ["   "]}), "x")

    def test_none_value_raises_by_default(self) -> None:
        df = pd.DataFrame({"x": pd.Series([None], dtype="object")})
        with pytest.raises(ValueError, match="unparseable value"):
            ops.coerce_numeric(df, "x")

    def test_garbage_text_raises_by_default(self) -> None:
        with pytest.raises(ValueError, match="unparseable value"):
            ops.coerce_numeric(pd.DataFrame({"x": ["not-a-number"]}), "x")

    def test_allow_null_converts_unparseable_to_nan(self) -> None:
        df = pd.DataFrame({"x": ["100", "", "garbage"]})
        result = ops.coerce_numeric(df, "x", allow_null=True)
        assert result["x"].iloc[0] == 100.0
        assert math.isnan(result["x"].iloc[1])
        assert math.isnan(result["x"].iloc[2])

    def test_preserves_row_marker(self) -> None:
        df = pd.DataFrame({"x": ["1", "2"], "_src_row": [5, 6]})
        result = ops.coerce_numeric(df, "x")
        assert result["_src_row"].tolist() == [5, 6]


class TestCoerceDate:
    def test_parses_string_format_into_iso_date(self) -> None:
        df = pd.DataFrame({"x": ["01/15/2019", "12/31/2020"]})
        result = ops.coerce_date(df, "x", fmt="%m/%d/%Y")
        assert result["x"].tolist() == ["2019-01-15", "2020-12-31"]

    def test_parses_excel_serial_into_iso_date(self) -> None:
        df = pd.DataFrame({"x": [43480]})
        result = ops.coerce_date(df, "x")
        assert result["x"].tolist() == ["2019-01-15"]

    def test_preserves_row_marker(self) -> None:
        df = pd.DataFrame({"x": [43480], "_src_row": [9]})
        result = ops.coerce_date(df, "x")
        assert result["_src_row"].tolist() == [9]


class TestSelectSheet:
    def test_returns_the_named_sheet(self) -> None:
        sheets = {"Results": pd.DataFrame({"x": [1]}), "Other": pd.DataFrame({"y": [2]})}
        result = ops.select_sheet(sheets, "Results")
        pd.testing.assert_frame_equal(result, sheets["Results"])

    def test_missing_sheet_raises_with_available_names(self) -> None:
        sheets = {"Results": pd.DataFrame({"x": [1]})}
        with pytest.raises(KeyError, match="available sheets: Results"):
            ops.select_sheet(sheets, "Missing")


class TestUnpivot:
    def test_duplicates_row_marker_across_exploded_rows(self) -> None:
        df = pd.DataFrame({"id": ["a"], "q1": [1], "q2": [2], "_src_row": [5]})
        result = ops.unpivot(
            df, id_vars=["id"], value_vars=["q1", "q2"], var_name="quarter", value_name="amount"
        )
        assert result["_src_row"].tolist() == [5, 5]
        assert result["quarter"].tolist() == ["q1", "q2"]
        assert result["amount"].tolist() == [1, 2]

    def test_row_marker_already_in_id_vars_is_not_duplicated(self) -> None:
        df = pd.DataFrame({"id": ["a"], "q1": [1], "_src_row": [5]})
        result = ops.unpivot(
            df,
            id_vars=["id", "_src_row"],
            value_vars=["q1"],
            var_name="quarter",
            value_name="amount",
        )
        assert list(result.columns) == ["id", "_src_row", "quarter", "amount"]


class TestSplitColumn:
    def test_splits_into_named_columns_preserving_row_marker(self) -> None:
        df = pd.DataFrame({"x": ["10Y-2.5"], "_src_row": [3]})
        result = ops.split_column(df, "x", into=["term", "rate"], pattern=r"(\d+Y)-(\d+\.\d+)")
        expected = pd.DataFrame({"term": ["10Y"], "rate": ["2.5"], "_src_row": [3]})
        pd.testing.assert_frame_equal(result, expected)

    def test_wrong_capture_group_count_raises(self) -> None:
        df = pd.DataFrame({"x": ["10Y-2.5"], "_src_row": [3]})
        with pytest.raises(ValueError, match="has 2 capture groups"):
            ops.split_column(df, "x", into=["only_one"], pattern=r"(\d+Y)-(\d+\.\d+)")

    def test_unmatched_rows_raise_listing_row_marker(self) -> None:
        df = pd.DataFrame({"x": ["10Y-2.5", "garbage"], "_src_row": [3, 4]})
        with pytest.raises(ValueError, match=r"_src_row=\[4\]"):
            ops.split_column(df, "x", into=["term", "rate"], pattern=r"(\d+Y)-(\d+\.\d+)")

    def test_unmatched_rows_without_row_marker_column_use_index(self) -> None:
        df = pd.DataFrame({"x": ["10Y-2.5", "garbage"]})
        with pytest.raises(ValueError, match=r"_src_row=\[1\]"):
            ops.split_column(df, "x", into=["term", "rate"], pattern=r"(\d+Y)-(\d+\.\d+)")


class TestMapValues:
    def test_remaps_known_values(self) -> None:
        df = pd.DataFrame({"x": ["A", "B"]})
        result = ops.map_values(df, "x", {"A": "Alpha", "B": "Beta"})
        assert result["x"].tolist() == ["Alpha", "Beta"]

    def test_unmapped_value_raises_by_default(self) -> None:
        df = pd.DataFrame({"x": ["A", "Z"]})
        with pytest.raises(ValueError, match="no mapping: \\['Z'\\]"):
            ops.map_values(df, "x", {"A": "Alpha"})

    def test_on_missing_keep_leaves_unmapped_values_unchanged(self) -> None:
        df = pd.DataFrame({"x": ["A", "Z"]})
        result = ops.map_values(df, "x", {"A": "Alpha"}, on_missing="keep")
        assert result["x"].tolist() == ["Alpha", "Z"]


class TestFilterRows:
    @pytest.mark.parametrize(
        ("op", "value", "expected"),
        [
            ("eq", 2, [2]),
            ("ne", 2, [1, 3]),
            ("gt", 1, [2, 3]),
            ("gte", 2, [2, 3]),
            ("lt", 2, [1]),
            ("lte", 2, [1, 2]),
            ("isin", [1, 3], [1, 3]),
        ],
    )
    def test_comparison_ops(self, op: str, value: object, expected: list[int]) -> None:
        df = pd.DataFrame({"x": [1, 2, 3]})
        result = ops.filter_rows(df, "x", op, value)
        assert result["x"].tolist() == expected

    def test_notna_and_isna(self) -> None:
        df = pd.DataFrame({"x": [1, None, 3]})
        assert ops.filter_rows(df, "x", "notna")["x"].tolist() == [1.0, 3.0]
        assert math.isnan(ops.filter_rows(df, "x", "isna")["x"].iloc[0])

    def test_unknown_op_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown op"):
            ops.filter_rows(pd.DataFrame({"x": [1]}), "x", "bogus")

    def test_preserves_row_marker(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3], "_src_row": [10, 11, 12]})
        result = ops.filter_rows(df, "x", "gt", 1)
        assert result["_src_row"].tolist() == [11, 12]


class TestAssertUnique:
    def test_passes_through_unique_frame_unchanged(self) -> None:
        df = pd.DataFrame({"id": [1, 2]})
        result = ops.assert_unique(df, ["id"])
        pd.testing.assert_frame_equal(result, df)

    def test_duplicate_key_raises(self) -> None:
        df = pd.DataFrame({"id": [1, 1, 2]})
        with pytest.raises(ValueError, match="not unique"):
            ops.assert_unique(df, ["id"])


class TestTagVerification:
    @pytest.mark.parametrize("value", ["VERIFIED", "TO_VERIFY", "DERIVED"])
    def test_stamps_constant_column(self, value: str) -> None:
        df = pd.DataFrame({"x": [1, 2]})
        result = ops.tag_verification(df, value)
        assert result["_verification"].tolist() == [value, value]

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError, match="not in"):
            ops.tag_verification(pd.DataFrame({"x": [1]}), "MAYBE")

    def test_custom_column_name(self) -> None:
        df = pd.DataFrame({"x": [1]})
        result = ops.tag_verification(df, "VERIFIED", column="status")
        assert result["status"].tolist() == ["VERIFIED"]


class TestDerive:
    def test_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="spec.md §7.3"):
            ops.derive(pd.DataFrame({"x": [1]}), "y", "x + 1")
