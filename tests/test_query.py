"""Unit tests for src/naru/query.py."""

import sqlite3
from pathlib import Path

import pytest

from naru import query

VALID_RECIPE = """\
---
name: greet
description: sanity check recipe
params:
  who:
    type: string
expected_columns: [greeting]
---
SELECT 'hello ' || :who AS greeting
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


class TestLoadRecipe:
    def test_valid_recipe_loads(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "greet.sql", VALID_RECIPE)
        recipe = query.load_recipe(path)
        assert recipe.name == "greet"
        assert recipe.description == "sanity check recipe"
        assert recipe.params == {"who": query.RecipeParam(type="string")}
        assert recipe.expected_columns == ["greeting"]
        assert recipe.sql == "SELECT 'hello ' || :who AS greeting"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(query.RecipeLoadError, match="file not found"):
            query.load_recipe(tmp_path / "missing.sql")

    def test_missing_opening_delimiter_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "bad.sql", "name: x\n---\nSELECT 1\n")
        with pytest.raises(query.RecipeLoadError, match="must start with"):
            query.load_recipe(path)

    def test_unclosed_front_matter_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "bad.sql", "---\nname: x\n")
        with pytest.raises(query.RecipeLoadError, match="never closed"):
            query.load_recipe(path)

    def test_invalid_yaml_front_matter_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "bad.sql", "---\nname: [unclosed\n---\nSELECT 1\n")
        with pytest.raises(query.RecipeLoadError, match="invalid YAML"):
            query.load_recipe(path)

    def test_front_matter_not_a_mapping_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "bad.sql", "---\n- a\n- b\n---\nSELECT 1\n")
        with pytest.raises(query.RecipeLoadError, match="must be a YAML mapping"):
            query.load_recipe(path)

    def test_schema_violation_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "bad.sql", "---\nname: x\nexpected_columns: not_a_list\n---\nSELECT 1\n"
        )
        with pytest.raises(query.RecipeLoadError, match="bad.sql"):
            query.load_recipe(path)

    def test_recipe_with_no_params_loads(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "noparam.sql",
            "---\nname: noparam\nexpected_columns: [one]\n---\nSELECT 1 AS one\n",
        )
        recipe = query.load_recipe(path)
        assert recipe.params == {}


class TestListAndFindRecipes:
    def test_list_recipes_sorted_by_path(self, tmp_path: Path) -> None:
        _write(tmp_path / "b.sql", "---\nname: b\nexpected_columns: [x]\n---\nSELECT 1 AS x\n")
        _write(tmp_path / "a.sql", "---\nname: a\nexpected_columns: [x]\n---\nSELECT 1 AS x\n")
        recipes = query.list_recipes(tmp_path)
        assert [r.name for r in recipes] == ["a", "b"]

    def test_list_recipes_recurses_subdirectories(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        _write(sub / "nested.sql", "---\nname: nested\nexpected_columns: [x]\n---\nSELECT 1 AS x\n")
        recipes = query.list_recipes(tmp_path)
        assert [r.name for r in recipes] == ["nested"]

    def test_find_recipe_by_name(self, tmp_path: Path) -> None:
        _write(tmp_path / "greet.sql", VALID_RECIPE)
        recipe = query.find_recipe(tmp_path, "greet")
        assert recipe.name == "greet"

    def test_find_recipe_missing_name_raises(self, tmp_path: Path) -> None:
        _write(tmp_path / "greet.sql", VALID_RECIPE)
        with pytest.raises(query.RecipeLoadError, match="no recipe named 'nope'"):
            query.find_recipe(tmp_path, "nope")


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE t (id INTEGER, name TEXT, amount REAL)")
    connection.executemany(
        "INSERT INTO t (id, name, amount) VALUES (?, ?, ?)",
        [(1, "a", 10.5), (2, "b", 20.0)],
    )
    connection.commit()
    return connection


class TestRunRecipe:
    def test_binds_param_and_returns_rows(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        path = _write(
            tmp_path / "by_name.sql",
            "---\nname: by_name\nparams:\n  who:\n    type: string\n"
            "expected_columns: [id, name, amount]\n---\n"
            "SELECT id, name, amount FROM t WHERE name = :who\n",
        )
        recipe = query.load_recipe(path)
        result = query.run_recipe(conn, recipe, {"who": "a"})
        assert result.columns == ["id", "name", "amount"]
        assert result.rows == [{"id": 1, "name": "a", "amount": 10.5}]

    def test_param_value_is_never_interpreted_as_sql(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        path = _write(
            tmp_path / "echo.sql",
            "---\nname: echo\nparams:\n  who:\n    type: string\n"
            "expected_columns: [echoed]\n---\nSELECT :who AS echoed\n",
        )
        recipe = query.load_recipe(path)
        malicious = "'; DROP TABLE t; --"
        result = query.run_recipe(conn, recipe, {"who": malicious})
        assert result.rows == [{"echoed": malicious}]
        # table t must still exist and be untouched
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert count == 2

    def test_missing_param_raises(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        path = _write(
            tmp_path / "needs_param.sql",
            "---\nname: needs_param\nparams:\n  who:\n    type: string\n"
            "expected_columns: [id]\n---\nSELECT id FROM t WHERE name = :who\n",
        )
        recipe = query.load_recipe(path)
        with pytest.raises(query.QueryParamError, match="missing: \\['who'\\]"):
            query.run_recipe(conn, recipe, {})

    def test_extra_param_raises(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        path = _write(
            tmp_path / "noparam.sql",
            "---\nname: noparam\nexpected_columns: [id]\n---\nSELECT id FROM t\n",
        )
        recipe = query.load_recipe(path)
        with pytest.raises(query.QueryParamError, match="unexpected: \\['bogus'\\]"):
            query.run_recipe(conn, recipe, {"bogus": "x"})

    def test_unparseable_integer_param_raises(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        path = _write(
            tmp_path / "int_param.sql",
            "---\nname: int_param\nparams:\n  n:\n    type: integer\n"
            "expected_columns: [id]\n---\nSELECT id FROM t WHERE id = :n\n",
        )
        recipe = query.load_recipe(path)
        with pytest.raises(query.QueryParamError, match="int_param"):
            query.run_recipe(conn, recipe, {"n": "not-a-number"})

    def test_float_param_coerced(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        path = _write(
            tmp_path / "float_param.sql",
            "---\nname: float_param\nparams:\n  min_amount:\n    type: float\n"
            "expected_columns: [id]\n---\nSELECT id FROM t WHERE amount >= :min_amount\n",
        )
        recipe = query.load_recipe(path)
        result = query.run_recipe(conn, recipe, {"min_amount": "15.0"})
        assert result.rows == [{"id": 2}]

    def test_boolean_param_true_variants(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        path = _write(
            tmp_path / "bool_param.sql",
            "---\nname: bool_param\nparams:\n  flag:\n    type: boolean\n"
            "expected_columns: [flag_value]\n---\nSELECT :flag AS flag_value\n",
        )
        recipe = query.load_recipe(path)
        assert query.run_recipe(conn, recipe, {"flag": "true"}).rows[0]["flag_value"] == 1
        assert query.run_recipe(conn, recipe, {"flag": "0"}).rows[0]["flag_value"] == 0

    def test_invalid_boolean_param_raises(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        path = _write(
            tmp_path / "bool_param.sql",
            "---\nname: bool_param\nparams:\n  flag:\n    type: boolean\n"
            "expected_columns: [flag_value]\n---\nSELECT :flag AS flag_value\n",
        )
        recipe = query.load_recipe(path)
        with pytest.raises(query.QueryParamError, match="not a valid boolean"):
            query.run_recipe(conn, recipe, {"flag": "maybe"})

    def test_valid_date_param_passes_through_as_iso_string(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        path = _write(
            tmp_path / "date_param.sql",
            "---\nname: date_param\nparams:\n  as_of:\n    type: date\n"
            "expected_columns: [as_of_value]\n---\nSELECT :as_of AS as_of_value\n",
        )
        recipe = query.load_recipe(path)
        result = query.run_recipe(conn, recipe, {"as_of": "2024-01-31"})
        assert result.rows == [{"as_of_value": "2024-01-31"}]

    def test_invalid_date_param_raises(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        path = _write(
            tmp_path / "date_param.sql",
            "---\nname: date_param\nparams:\n  as_of:\n    type: date\n"
            "expected_columns: [as_of_value]\n---\nSELECT :as_of AS as_of_value\n",
        )
        recipe = query.load_recipe(path)
        with pytest.raises(query.QueryParamError, match="date_param"):
            query.run_recipe(conn, recipe, {"as_of": "not-a-date"})

    def test_shape_mismatch_raises(self, tmp_path: Path, conn: sqlite3.Connection) -> None:
        path = _write(
            tmp_path / "wrong_shape.sql",
            "---\nname: wrong_shape\nexpected_columns: [id, extra_column]\n---\nSELECT id FROM t\n",
        )
        recipe = query.load_recipe(path)
        with pytest.raises(query.QueryShapeError, match="expected columns"):
            query.run_recipe(conn, recipe, {})

    def test_sql_execution_error_raises_query_error(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        path = _write(
            tmp_path / "broken.sql",
            "---\nname: broken\nexpected_columns: [id]\n---\nSELECT id FROM no_such_table\n",
        )
        recipe = query.load_recipe(path)
        with pytest.raises(query.QueryError, match="failed to execute"):
            query.run_recipe(conn, recipe, {})

    def test_no_rows_matches_expected_columns(
        self, tmp_path: Path, conn: sqlite3.Connection
    ) -> None:
        path = _write(
            tmp_path / "no_rows.sql",
            "---\nname: no_rows\nexpected_columns: [id]\n---\nSELECT id FROM t WHERE id = 999\n",
        )
        recipe = query.load_recipe(path)
        result = query.run_recipe(conn, recipe, {})
        assert result.rows == []
        assert result.columns == ["id"]


class TestRenderRows:
    def test_no_rows(self) -> None:
        result = query.QueryResult(columns=["id"], rows=[])
        assert query.render_rows(result) == "(no rows)"

    def test_aligned_table(self) -> None:
        result = query.QueryResult(
            columns=["name", "amount"],
            rows=[{"name": "a", "amount": 10.5}, {"name": "longname", "amount": 2}],
        )
        text = query.render_rows(result)
        lines = text.splitlines()
        assert lines[0].startswith("name")
        assert all(len(line) == len(lines[0]) for line in lines)
