"""Unit tests for src/naru/ops.py."""

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


class TestDropBlankRows:
    def test_drops_rows_where_every_business_column_is_null(self) -> None:
        df = pd.DataFrame(
            {
                "x": ["a", None, "c"],
                "y": ["b", None, "d"],
                "_src_row": [1, 2, 3],
            }
        )
        result = ops.drop_blank_rows(df)
        expected = pd.DataFrame({"x": ["a", "c"], "y": ["b", "d"], "_src_row": [1, 3]})
        pd.testing.assert_frame_equal(result, expected)

    def test_keeps_rows_with_at_least_one_non_null_business_value(self) -> None:
        df = pd.DataFrame({"x": ["a", None], "y": [None, None], "_src_row": [1, 2]})
        result = ops.drop_blank_rows(df)
        expected = pd.DataFrame({"x": ["a"], "y": [None], "_src_row": [1]})
        pd.testing.assert_frame_equal(result, expected)

    def test_ignores_row_marker_column_when_checking_blankness(self) -> None:
        df = pd.DataFrame({"x": [None], "_src_row": [1]})
        result = ops.drop_blank_rows(df)
        assert result.empty


class TestCoerceThousands:
    def test_strips_commas_and_converts_to_float(self) -> None:
        df = pd.DataFrame({"x": ["38,000", "1,234"]})
        result = ops.coerce_thousands(df, "x")
        assert result["x"].tolist() == [38000.0, 1234.0]
        assert result["x"].dtype == "float64"

    def test_handles_values_without_commas(self) -> None:
        df = pd.DataFrame({"x": ["500"]})
        result = ops.coerce_thousands(df, "x")
        assert result["x"].tolist() == [500.0]


class TestCoercePercent:
    def test_strips_percent_sign_and_divides_by_100(self) -> None:
        df = pd.DataFrame({"x": ["2.747%", "0%"]})
        result = ops.coerce_percent(df, "x")
        assert result["x"].tolist() == pytest.approx([0.02747, 0.0])


class TestCoerceFloat:
    def test_converts_object_dtype_column_to_float64(self) -> None:
        df = pd.DataFrame({"x": pd.Series([1.5, 2.5], dtype="object")})
        result = ops.coerce_float(df, "x")
        assert result["x"].dtype == "float64"
        assert result["x"].tolist() == [1.5, 2.5]


class TestParseDateString:
    def test_parses_mm_dd_yyyy_into_iso_date_strings(self) -> None:
        df = pd.DataFrame({"x": ["01/15/2019", "12/31/2020"]})
        result = ops.parse_date_string(df, "x", fmt="%m/%d/%Y")
        assert result["x"].tolist() == ["2019-01-15", "2020-12-31"]


class TestParseExcelSerialDate:
    def test_parses_serial_number_into_iso_date_string(self) -> None:
        df = pd.DataFrame({"x": [43480]})
        result = ops.parse_excel_serial_date(df, "x")
        assert result["x"].tolist() == ["2019-01-15"]
