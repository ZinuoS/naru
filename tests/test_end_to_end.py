"""End-to-end test of the full §2.7 loop: map suggest -> human approval
-> mirror --dry-run -> commit -> re-run against the same file (clean
duplicate-key abort) -> naru map learn -> a second suggest run picking up
the newly learned synonym. Uses the synthetic client_statement.xlsx
fixture (tests/fixtures/generators/make_client_statement.py), whose
column names and units deliberately differ from the warehouse schema.
"""

import json
import sqlite3
from pathlib import Path

from naru import map_suggest, mapping, mirror
from naru.profiler import profile

FIXTURE = Path(__file__).parent / "fixtures" / "client_statement.xlsx"

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


def test_full_mapping_and_mirror_lifecycle(tmp_path: Path) -> None:
    synonyms_path = tmp_path / "synonyms.yaml"
    map_suggest.save_synonyms({"cpn": "coupon_rate"}, synonyms_path)

    sheet_profile = profile(FIXTURE).sheets[0]

    # --- first suggest: tiers 1-2 auto-match 3 of 5 columns ---
    draft, proposals = map_suggest.suggest(
        sheet_profile,
        _target_row_cls(),
        target="warehouse.positions",
        key=["deal_id", "as_of"],
        synonyms_path=synonyms_path,
    )
    by_source = {c.source: c for c in draft.columns}
    assert set(by_source) == {"Deal ID", "Cpn (%)", "As Of"}
    assert by_source["Deal ID"].basis == "exact"
    assert by_source["Cpn (%)"].basis == "synonym"
    assert by_source["As Of"].basis == "exact"
    assert all(not c.approved for c in draft.columns)

    proposed_sources = {p.source for p in proposals}
    assert proposed_sources == {"Broker", "Notes"}
    assert all(p.target is None and p.evidence == "" for p in proposals)

    # --- human review: approve the auto-matches, add the transform for
    # coupon_rate, and manually resolve "Broker" -> counterparty (basis:
    # llm) -- a stand-in for what a real tier 3/4 or a human would do. ---
    for column in draft.columns:
        column.approved = True
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
    # "Notes" is deliberately left unmapped.

    artifact_dir = tmp_path / "mapping_artifact"
    artifact_dir.mkdir()
    (artifact_dir / "mapping.yaml").write_text(mapping.to_yaml(draft))
    (artifact_dir / "fingerprint.json").write_text(json.dumps(FINGERPRINT))
    (artifact_dir / "schema.py").write_text(TARGET_ROW_SCHEMA)

    db_path = tmp_path / "naru.sqlite"
    raw_dir = tmp_path / "raw"

    # --- dry run ---
    dry_result = mirror.mirror(artifact_dir, FIXTURE, db_path, raw_dir, dry_run=True)
    assert dry_result.summary.rows_in == 5
    assert dry_result.summary.rows_out == 5
    assert dry_result.summary.unmapped_source_columns == ["Notes"]
    assert dry_result.summary.target_columns_not_populated == ["trade_date"]
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM warehouse_positions").fetchone()[0] == 0
    conn.close()

    # --- commit ---
    commit_result = mirror.mirror(artifact_dir, FIXTURE, db_path, raw_dir, dry_run=False)
    assert len(commit_result.row_ids) == 5
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT deal_id, counterparty FROM warehouse_positions ORDER BY deal_id"
    ).fetchall()
    assert [r["deal_id"] for r in rows] == ["D001", "D002", "D003", "D004", "D005"]
    assert all(r["counterparty"] for r in rows)
    conn.close()

    # --- re-mirror the same file: clean duplicate-key abort ---
    try:
        mirror.mirror(artifact_dir, FIXTURE, db_path, raw_dir, dry_run=False)
        raise AssertionError("expected MirrorDuplicateKeyError")
    except mirror.MirrorDuplicateKeyError as exc:
        assert len(exc.colliding_with_existing) == 5
        assert exc.colliding_within_batch == []

    # --- naru map learn: promote the approved synonym/llm matches ---
    approved_mapping = mapping.load_mapping(artifact_dir / "mapping.yaml")
    report = map_suggest.map_learn(approved_mapping, synonyms_path=synonyms_path)
    assert report.added == {"broker": "counterparty"}
    assert map_suggest.load_synonyms(synonyms_path)["broker"] == "counterparty"

    # --- second suggest: "Broker" now resolves automatically via tier 2 ---
    draft2, proposals2 = map_suggest.suggest(
        sheet_profile,
        _target_row_cls(),
        target="warehouse.positions",
        key=["deal_id", "as_of"],
        synonyms_path=synonyms_path,
    )
    by_source2 = {c.source: c for c in draft2.columns}
    assert by_source2["Broker"].basis == "synonym"
    assert by_source2["Broker"].target == "counterparty"
    assert {p.source for p in proposals2} == {"Notes"}


def _target_row_cls() -> type:
    from pydantic import BaseModel

    class TargetRow(BaseModel):
        deal_id: str
        coupon_rate: float
        as_of: str
        counterparty: str
        trade_date: str | None = None

    return TargetRow
