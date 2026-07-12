#!/usr/bin/env python3
"""Live demo of the full §2.7 loop: map suggest -> human approval ->
mirror --dry-run -> commit -> re-run against the same file (clean
duplicate-key abort) -> naru map learn -> a second suggest run that picks
up the newly learned synonym automatically.

Uses the synthetic client_statement.xlsx fixture (tests/fixtures/
generators/make_client_statement.py), whose column names and units
deliberately differ from the warehouse schema it's mirrored into. Runs
entirely in a temporary directory; leaves nothing behind.

Usage: python scripts/demo_mirror.py
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pydantic import BaseModel  # noqa: E402

from naru import map_suggest, mapping, mirror  # noqa: E402
from naru.profiler import profile  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "client_statement.xlsx"

TARGET_ROW_SCHEMA = """\
from pydantic import BaseModel


class TargetRow(BaseModel):
    deal_id: str
    coupon_rate: float
    as_of: str
    counterparty: str
    trade_date: str | None = None
"""

FINGERPRINT = {
    "sheet": "Statement",
    "header_row": 1,
    "columns": [
        {"name": "Deal ID", "type": "string", "strictness": "strict"},
        {"name": "Cpn (%)", "type": "float", "strictness": "strict"},
        {"name": "As Of", "type": "string", "strictness": "strict"},
        {"name": "Broker", "type": "string", "strictness": "strict"},
        {"name": "Notes", "type": "string", "strictness": "strict"},
    ],
}


class TargetRow(BaseModel):
    deal_id: str
    coupon_rate: float
    as_of: str
    counterparty: str
    trade_date: str | None = None


def heading(text: str) -> None:
    print()
    print(text)
    print("-" * len(text))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        synonyms_path = tmp_path / "synonyms.yaml"
        map_suggest.save_synonyms({"cpn": "coupon_rate"}, synonyms_path)

        heading("1. Profile the client's file")
        sheet_profile = profile(FIXTURE).sheets[0]
        print(f"sheet {sheet_profile.name!r}: {[c.header_text for c in sheet_profile.columns]}")

        heading("2. naru map suggest (first run)")
        print(f"synonym dictionary before: {map_suggest.load_synonyms(synonyms_path)}")
        draft, proposals = map_suggest.suggest(
            sheet_profile,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id", "as_of"],
            synonyms_path=synonyms_path,
        )
        for column in draft.columns:
            print(f"  matched   {column.source!r:14} -> {column.target:14} (basis: {column.basis})")
        for proposal in proposals:
            print(
                f"  unmatched {proposal.source!r:14} -> tier {proposal.basis} stub: "
                f"target={proposal.target}, evidence={proposal.evidence!r}"
            )

        heading("3. Human review: approve tiers 1-2, resolve Broker by hand")
        for column in draft.columns:
            column.approved = True
        by_source = {c.source: c for c in draft.columns}
        by_source["Cpn (%)"].transform = "coerce_numeric(scale=0.01)"
        draft.columns.append(
            mapping.ColumnMapping(
                source="Broker",
                target="counterparty",
                transform="",
                basis="llm",
                evidence="client's 'Broker' column consistently names the "
                "counterparty on the other side of the trade",
                approved=True,
            )
        )
        print("Broker -> counterparty added by hand (basis: llm); Notes left unmapped.")

        artifact_dir = tmp_path / "mapping_artifact"
        artifact_dir.mkdir()
        (artifact_dir / "mapping.yaml").write_text(mapping.to_yaml(draft))
        (artifact_dir / "fingerprint.json").write_text(json.dumps(FINGERPRINT))
        (artifact_dir / "schema.py").write_text(TARGET_ROW_SCHEMA)
        print(f"wrote frozen mapping.yaml to {artifact_dir / 'mapping.yaml'}")

        db_path = tmp_path / "naru.sqlite"
        raw_dir = tmp_path / "raw"

        heading("4. naru mirror --dry-run")
        dry_result = mirror.mirror(artifact_dir, FIXTURE, db_path, raw_dir, dry_run=True)
        print(dry_result.summary.render())

        heading("5. naru mirror (commit)")
        commit_result = mirror.mirror(artifact_dir, FIXTURE, db_path, raw_dir, dry_run=False)
        print(commit_result.summary.render())
        print(f"\nrun_id={commit_result.run_id}  row_ids={commit_result.row_ids}")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        print("\nwarehouse_positions:")
        for row in conn.execute("SELECT * FROM warehouse_positions ORDER BY deal_id"):
            print(f"  {dict(row)}")
        conn.close()

        heading("6. Re-mirror the same file (expect a clean duplicate-key abort)")
        try:
            mirror.mirror(artifact_dir, FIXTURE, db_path, raw_dir, dry_run=False)
            print("ERROR: expected MirrorDuplicateKeyError, none raised")
        except mirror.MirrorDuplicateKeyError as exc:
            print(f"Aborted, nothing written: {exc}")

        heading("7. naru map learn")
        approved_mapping = mapping.load_mapping(artifact_dir / "mapping.yaml")
        report = map_suggest.map_learn(approved_mapping, synonyms_path=synonyms_path)
        print(f"added: {report.added}")
        print(f"skipped_conflicts: {report.skipped_conflicts}")
        print(f"synonym dictionary after: {map_suggest.load_synonyms(synonyms_path)}")

        heading("8. naru map suggest (second run) -- the flywheel")
        draft2, proposals2 = map_suggest.suggest(
            sheet_profile,
            TargetRow,
            target="warehouse.positions",
            key=["deal_id", "as_of"],
            synonyms_path=synonyms_path,
        )
        for column in draft2.columns:
            print(f"  matched   {column.source!r:14} -> {column.target:14} (basis: {column.basis})")
        for proposal in proposals2:
            print(
                f"  unmatched {proposal.source!r:14} -> tier {proposal.basis} stub: still no target"
            )
        assert by_source_target(draft2, "Broker") == "counterparty (basis: synonym)"
        print("\nBroker matched automatically this time -- no human needed.")


def by_source_target(m: mapping.Mapping, source: str) -> str:
    for column in m.columns:
        if column.source == source:
            return f"{column.target} (basis: {column.basis})"
    raise AssertionError(f"{source!r} not found in mapping")


if __name__ == "__main__":
    main()
