"""CLI entry point: python -m naru <subcommand> ...

  naru run <artifact> <input> [--as-of YYYY-MM-DD]

Exit codes: 0 success; 3 fingerprint drift (spec.md §2.3) -- also writes
drift_report.json in the current directory; 4 output/validation failure;
1 anything else uncaught (a real bug, not a modeled failure mode).
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from naru.runtime import FingerprintDriftError, RuntimeCheckError, run

DB_PATH = Path("naru.sqlite")
RAW_DIR = Path(".naru/raw")
DRIFT_REPORT_PATH = Path("drift_report.json")


def _drift_report(
    artifact_path: Path, input_path: Path, exc: FingerprintDriftError
) -> dict[str, object]:
    """Structured drift report: designed to be read by a human and pasted
    back into a design-time recompilation session, per spec.md §2.3.
    """
    return {
        "artifact": str(artifact_path),
        "input_file": str(input_path),
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
        ],
    }


def _cmd_run(args: argparse.Namespace) -> int:
    artifact_path = Path(args.artifact)
    input_path = Path(args.input)
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None

    try:
        result = run(
            artifact_path=artifact_path,
            input_path=input_path,
            db_path=DB_PATH,
            raw_dir=RAW_DIR,
            as_of=as_of,
        )
    except FingerprintDriftError as exc:
        report = _drift_report(artifact_path, input_path, exc)
        DRIFT_REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"fingerprint drift detected; see {DRIFT_REPORT_PATH}", file=sys.stderr)
        for difference in exc.result.differences:
            print(f"  {difference.message()}", file=sys.stderr)
        return 3
    except RuntimeCheckError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 4

    print(f"run_id={result.run_id} rows_loaded={len(result.row_ids)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="naru")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run a pipeline artifact against an input file")
    run_parser.add_argument("artifact")
    run_parser.add_argument("input")
    run_parser.add_argument(
        "--as-of", default=None, help="ISO date, stored as given, never inferred"
    )
    run_parser.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
