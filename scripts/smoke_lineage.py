"""Opt-in live DSH lineage test with synthetic schema/SQL; no database access."""

import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metadata_service.config import ROOT, Settings
from metadata_service.runner import run_task
from metadata_service.store import Store, uid


def main():
    settings = Settings(data_dir=ROOT / "data" / ("lineage-smoke-" + uid())).initialize()
    store = Store(settings)
    path = settings.data_dir / "synthetic.xlsx"
    book = openpyxl.Workbook()
    book.active.append(["Synthetic confirmed fixture; use SQL evidence attached to tables."])
    book.save(path)
    source = store.create("source", {"type": "excel", "name": "synthetic.xlsx", "path": str(path)})
    job_id = store.new_job(source)
    store.claim(job_id)
    sql = "CREATE VIEW v_users AS WITH u AS (SELECT id, amount FROM users) SELECT id AS user_id, amount * 2 AS doubled FROM u"
    ref = store.evidence(job_id, "sql_definition", {"name": "v_users", "kind": "view"}, {"sql": sql})
    findings = {
        "tables": [
            {
                "name": "users",
                "columns": [{"name": "id", "type": "BIGINT"}, {"name": "amount", "type": "DECIMAL"}],
                "evidence_ids": [ref],
            },
            {
                "name": "v_users",
                "kind": "view",
                "comment": "Analyze the SQL definition in this table's evidence; submit CTE and expression field lineage.",
                "columns": [{"name": "user_id", "type": "BIGINT"}, {"name": "doubled", "type": "DECIMAL"}],
                "evidence_ids": [ref],
            },
        ]
    }
    store.save_draft(job_id, findings)
    store.update(job_id, confirmed=True)
    run_task(store, job_id)
    job = store.get(job_id)
    assert job["status"] in ("succeeded", "partial_success"), (job["error"], str(settings.data_dir))
    edges = store.get(job["snapshot_id"], "snapshot")["findings"]["lineage"]
    actual = {(e["source_table"], e.get("source_column"), e["target_table"], e.get("target_column")) for e in edges}
    assert ("users", "id", "v_users", "user_id") in actual, (edges, str(settings.data_dir))
    assert ("users", "amount", "v_users", "doubled") in actual, (edges, str(settings.data_dir))
    assert all(ref in e["evidence_ids"] for e in edges)
    print("LIVE_DSH_CTE_AND_EXPRESSION_LINEAGE_OK", len(edges), settings.data_dir)


if __name__ == "__main__":
    main()
