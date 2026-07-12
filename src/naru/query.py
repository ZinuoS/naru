"""Query recipes: `naru query <name> --param k=v`, per docs/spec.md §2.6.

A recipe is a `.sql` file with YAML front matter (delimited by `---`
lines, like a Jekyll/markdown post) declaring its name, description,
typed params, and the exact result columns it promises:

    ---
    name: auction_tail
    description: ...
    params:
      security:
        type: string
    expected_columns: [auction_date, security_term, high_yield]
    ---
    SELECT auction_date, security_term, high_yield
    FROM final_auction_results
    WHERE security_term = :security
    ORDER BY auction_date;

Same compiler doctrine as everything else in this project: a human (or
an LLM at design time) authors and reviews the recipe once; only the
reviewed, frozen `.sql` file ever executes. `run_recipe` binds every
param through sqlite3's native named-placeholder style
(`cur.execute(sql, {"security": ...})`) -- there is no string
interpolation into the SQL text anywhere in this module, so a param
value containing SQL syntax is always treated as data, never code.
`expected_columns` is checked against the query's actual result columns
before any row is returned, so a recipe silently drifting out of sync
with a schema change is caught immediately, not discovered downstream.
"""

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

_FRONT_MATTER_DELIM = "---"


class QueryError(Exception):
    """Base class for every recipe-loading/execution error in this module."""


class RecipeLoadError(QueryError):
    """A malformed recipe file: missing/unclosed front matter, invalid
    YAML, or a front-matter shape that doesn't match RecipeFrontMatter.
    """


class QueryParamError(QueryError):
    """The params supplied to run_recipe don't match what the recipe
    declares -- missing, extra, or a value that doesn't parse as its
    declared type.
    """


class QueryShapeError(QueryError):
    """The recipe's SQL returned different columns than its
    expected_columns promise -- the recipe has drifted out of sync with
    the schema it queries.
    """


class RecipeParam(BaseModel):
    type: Literal["string", "integer", "float", "date", "boolean"]


class Recipe(BaseModel):
    name: str
    description: str = ""
    params: dict[str, RecipeParam] = {}
    expected_columns: list[str]
    sql: str


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, object]]


def _split_front_matter(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIM:
        raise RecipeLoadError(
            f"recipe must start with a {_FRONT_MATTER_DELIM!r} front-matter delimiter"
        )
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONT_MATTER_DELIM:
            front_matter = "".join(lines[1:i])
            sql = "".join(lines[i + 1 :])
            return front_matter, sql
    raise RecipeLoadError(
        f"recipe front matter never closed with a second {_FRONT_MATTER_DELIM!r} line"
    )


def load_recipe(path: Path) -> Recipe:
    """Load and validate a `.sql` recipe file's front matter + SQL body."""
    if not path.exists():
        raise RecipeLoadError(f"{path}: file not found")
    front_matter_text, sql = _split_front_matter(path.read_text())
    try:
        raw = yaml.safe_load(front_matter_text) or {}
    except yaml.YAMLError as exc:
        raise RecipeLoadError(f"{path}: invalid YAML front matter -- {exc}") from exc
    if not isinstance(raw, dict):
        raise RecipeLoadError(f"{path}: front matter must be a YAML mapping")
    raw["sql"] = sql.strip()
    try:
        return Recipe.model_validate(raw)
    except ValidationError as exc:
        raise RecipeLoadError(f"{path}: {exc}") from exc


def list_recipes(directory: Path) -> list[Recipe]:
    """Every recipe found under `directory`, recursively, sorted by path
    for deterministic ordering.
    """
    return [load_recipe(p) for p in sorted(directory.rglob("*.sql"))]


def find_recipe(directory: Path, name: str) -> Recipe:
    for recipe in list_recipes(directory):
        if recipe.name == name:
            return recipe
    raise RecipeLoadError(f"no recipe named {name!r} found under {directory}")


def _coerce_value(raw: str, param_type: str) -> object:
    if param_type == "string":
        return raw
    if param_type == "integer":
        return int(raw)
    if param_type == "float":
        return float(raw)
    if param_type == "boolean":
        lowered = raw.strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
        raise ValueError(f"{raw!r} is not a valid boolean (use true/false or 1/0)")
    if param_type == "date":
        dt.date.fromisoformat(raw)  # validates; raises ValueError with a clear message
        return raw
    raise AssertionError(f"unreachable: unknown param type {param_type!r}")  # pragma: no cover


def _coerce_params(recipe: Recipe, raw_params: dict[str, str]) -> dict[str, object]:
    provided = set(raw_params)
    expected = set(recipe.params)
    missing = expected - provided
    extra = provided - expected
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected: {sorted(extra)}")
        raise QueryParamError(f"recipe {recipe.name!r} param mismatch -- {'; '.join(parts)}")

    coerced: dict[str, object] = {}
    for key, raw_value in raw_params.items():
        try:
            coerced[key] = _coerce_value(raw_value, recipe.params[key].type)
        except ValueError as exc:
            raise QueryParamError(f"recipe {recipe.name!r} param {key!r}: {exc}") from exc
    return coerced


def run_recipe(
    conn: sqlite3.Connection, recipe: Recipe, raw_params: dict[str, str] | None = None
) -> QueryResult:
    """Execute a recipe's SQL with typed, named-placeholder param binding
    (never string interpolation), then validate the result's columns
    match `expected_columns` before returning any rows.
    """
    coerced = _coerce_params(recipe, raw_params or {})
    cur = conn.cursor()
    try:
        cur.execute(recipe.sql, coerced)
    except sqlite3.Error as exc:
        raise QueryError(f"recipe {recipe.name!r} failed to execute -- {exc}") from exc

    columns = [d[0] for d in cur.description] if cur.description else []
    if columns != recipe.expected_columns:
        raise QueryShapeError(
            f"recipe {recipe.name!r}: expected columns {recipe.expected_columns}, got {columns}"
        )
    rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    return QueryResult(columns=columns, rows=rows)


def render_rows(result: QueryResult) -> str:
    """Aligned, plain-text table -- for a human reading CLI output."""
    if not result.rows:
        return "(no rows)"
    widths = {
        column: max(len(column), max(len(str(row[column])) for row in result.rows))
        for column in result.columns
    }
    header = "  ".join(column.ljust(widths[column]) for column in result.columns)
    separator = "  ".join("-" * widths[column] for column in result.columns)
    lines = [header, separator]
    for row in result.rows:
        lines.append("  ".join(str(row[column]).ljust(widths[column]) for column in result.columns))
    return "\n".join(lines)
