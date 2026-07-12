"""naru CLI: `naru <command> ...`, per docs/spec.md.

Every command is a thin driver over an existing pure function elsewhere
in src/naru/ -- no business logic lives here (enforced by keeping this
file under ~200 lines). Exit codes are documented once, in
docs/exit_codes.md, and mean the same thing in every command: 0 ok,
1 setup/usage error, 2 validation/business-rule failure, 3 fingerprint
drift, 4 lint failure.
"""

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

import typer

from naru import __version__, map_suggest
from naru import mapping as mapping_module
from naru import query as query_lib
from naru.goldenharness import run_golden_test
from naru.lint import LintError, lint_artifact
from naru.mirror import MirrorDuplicateKeyError, MirrorError
from naru.mirror import mirror as mirror_fn
from naru.profiler import Profile, to_json
from naru.profiler import profile as profile_fn
from naru.runtime import FingerprintDriftError, RuntimeCheckError
from naru.runtime import run as run_fn

app = typer.Typer(no_args_is_help=True, help="Deterministic pipeline artifacts for messy files.")
map_app = typer.Typer(no_args_is_help=True, help="Design-time crosswalk suggestion and learning.")
app.add_typer(map_app, name="map")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"naru {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    pass


DEFAULT_DB_PATH = Path("naru.sqlite")
DEFAULT_RAW_DIR = Path(".naru/raw")
DRIFT_REPORT_PATH = Path("drift_report.json")

DbOption = Annotated[Path, typer.Option(help="SQLite database path.")]
RawDirOption = Annotated[Path, typer.Option(help="Raw zone directory.")]
SynonymsPathOption = Annotated[Path | None, typer.Option(help="Override ~/.naru/synonyms.yaml.")]
OutOption = Annotated[Path | None, typer.Option(help="Write here instead of stdout.")]
SheetOption = Annotated[str | None, typer.Option(help="Sheet name, if the profile has several.")]
RecipesDirOption = Annotated[Path, typer.Option(help="Directory to search for *.sql recipes.")]


def _emit(text: str, out: Path | None) -> None:
    if out:
        out.write_text(text)
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


def _write_drift_report(exc: FingerprintDriftError) -> None:
    report = {
        "differences": [
            {
                "kind": d.kind,
                "sheet": d.sheet,
                "column_position": d.column_position,
                "expected": d.expected,
                "found": d.found,
                "message": d.message(),
            }
            for d in exc.result.differences
        ]
    }
    DRIFT_REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    typer.echo(f"fingerprint drift detected; see {DRIFT_REPORT_PATH}", err=True)
    for d in exc.result.differences:
        typer.echo(f"  {d.message()}", err=True)


@app.command()
def profile(source: Path, out: OutOption = None) -> None:
    """Deterministic interrogation of an Excel file (spec.md §2.2)."""
    try:
        result = profile_fn(source)
    except (OSError, KeyError) as exc:
        typer.echo(f"could not profile {source}: {exc}", err=True)
        raise typer.Exit(1) from exc
    _emit(to_json(result), out)


@app.command()
def run(
    artifact: Path,
    input_file: Path,
    as_of: Annotated[str | None, typer.Option(help="ISO date, stored exactly as given.")] = None,
    db: DbOption = DEFAULT_DB_PATH,
    raw_dir: RawDirOption = DEFAULT_RAW_DIR,
) -> None:
    """Run a pipeline artifact against an input file (spec.md §2)."""
    as_of_date = dt.date.fromisoformat(as_of) if as_of else None
    try:
        result = run_fn(
            artifact_path=artifact,
            input_path=input_file,
            db_path=db,
            raw_dir=raw_dir,
            as_of=as_of_date,
        )
    except FingerprintDriftError as exc:
        _write_drift_report(exc)
        raise typer.Exit(3) from exc
    except RuntimeCheckError as exc:
        typer.echo(f"validation failed: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"run_id={result.run_id} rows_loaded={len(result.row_ids)}")


@app.command()
def test(artifact: Path) -> None:
    """Rerun an artifact's golden and compare against expected_output.parquet."""
    result = run_golden_test(artifact)
    differences = result.schema_differences + result.value_differences
    if differences:
        typer.echo(f"GOLDEN MISMATCH: {artifact}", err=True)
        for d in differences:
            typer.echo(f"  {d}", err=True)
        raise typer.Exit(2)
    typer.echo(f"golden test passed: {artifact}")


@app.command()
def lint(artifact: Path) -> None:
    """Static checks on a pipeline or Mapping Artifact directory."""
    try:
        findings = lint_artifact(artifact)
    except LintError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if findings:
        typer.echo(f"LINT FAILED: {artifact}", err=True)
        for f in findings:
            typer.echo(f"  {f.render()}", err=True)
        raise typer.Exit(4)
    typer.echo(f"lint passed: {artifact}")


@map_app.command("suggest")
def map_suggest_cmd(
    client_profile: Path,
    warehouse_schema: Path,
    target: Annotated[str, typer.Option(help="e.g. warehouse.positions")],
    key: Annotated[list[str], typer.Option(help="Natural key field; repeat --key for each.")],
    sheet: SheetOption = None,
    unmapped_source_columns: Annotated[Literal["warn", "fail"], typer.Option()] = "warn",
    synonyms_path: SynonymsPathOption = None,
    out: OutOption = None,
) -> None:
    """Propose a draft crosswalk via the tiered cascade (spec.md §2.7)."""
    try:
        loaded_profile = Profile.model_validate_json(client_profile.read_text())
        target_row = mapping_module._load_target_row(warehouse_schema)
    except (OSError, ValueError, mapping_module.MappingLoadError) as exc:
        typer.echo(f"could not load inputs: {exc}", err=True)
        raise typer.Exit(1) from exc

    if sheet:
        sheet_profile = next((s for s in loaded_profile.sheets if s.name == sheet), None)
        if sheet_profile is None:
            typer.echo(f"no sheet named {sheet!r} in {client_profile}", err=True)
            raise typer.Exit(1)
    elif len(loaded_profile.sheets) == 1:
        sheet_profile = loaded_profile.sheets[0]
    else:
        n = len(loaded_profile.sheets)
        typer.echo(f"{client_profile} has {n} sheets; pass --sheet", err=True)
        raise typer.Exit(1)

    draft, proposals = map_suggest.suggest(
        sheet_profile,
        target_row,
        target=target,
        key=key,
        unmapped_source_columns=unmapped_source_columns,
        synonyms_path=synonyms_path,
    )
    _emit(mapping_module.to_yaml(draft), out)
    for p in proposals:
        typer.echo(f"# still unmapped: {p.source!r} (tier {p.basis} stub)", err=True)


@map_app.command("learn")
def map_learn_cmd(
    mapping_yaml: Path,
    synonyms_path: SynonymsPathOption = None,
    force: Annotated[bool, typer.Option(help="Overwrite a conflicting existing entry.")] = False,
) -> None:
    """Promote human-approved synonym/llm matches into the synonym dictionary."""
    try:
        loaded = mapping_module.load_mapping(mapping_yaml)
    except mapping_module.MappingLoadError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    report = map_suggest.map_learn(loaded, synonyms_path=synonyms_path, force=force)
    typer.echo(f"added: {report.added}")
    typer.echo(f"skipped_conflicts: {report.skipped_conflicts}")


@app.command()
def mirror(
    mapping_artifact: Path,
    source_file: Path,
    commit: Annotated[bool, typer.Option(help="Write changes. Default is a dry run.")] = False,
    db: DbOption = DEFAULT_DB_PATH,
    raw_dir: RawDirOption = DEFAULT_RAW_DIR,
) -> None:
    """Mirror a client file into a SQL or Excel target (spec.md §2.7)."""
    try:
        result = mirror_fn(mapping_artifact, source_file, db, raw_dir, dry_run=not commit)
    except FingerprintDriftError as exc:
        _write_drift_report(exc)
        raise typer.Exit(3) from exc
    except (MirrorDuplicateKeyError, MirrorError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except mapping_module.MappingLoadError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.summary.render())
    if result.backup_path:
        typer.echo(f"backup: {result.backup_path}")


@app.command()
def query(
    name: str,
    recipes_dir: RecipesDirOption = Path("recipes"),
    db: DbOption = DEFAULT_DB_PATH,
    param: Annotated[list[str], typer.Option(help="k=v, repeat --param for each.")] = (),  # type: ignore[assignment]
) -> None:
    """Run a named, frozen query recipe (spec.md §2.6)."""
    try:
        recipe = query_lib.find_recipe(recipes_dir, name)
    except query_lib.RecipeLoadError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    try:
        raw_params = dict(p.split("=", 1) for p in param)
    except ValueError as exc:
        typer.echo(f"--param must be k=v: {exc}", err=True)
        raise typer.Exit(1) from exc

    conn = sqlite3.connect(db)
    try:
        result = query_lib.run_recipe(conn, recipe, raw_params)
    except query_lib.QueryParamError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except query_lib.QueryShapeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    finally:
        conn.close()
    typer.echo(query_lib.render_rows(result))


if __name__ == "__main__":  # pragma: no cover
    app()
