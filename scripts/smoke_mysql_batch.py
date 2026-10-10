"""Opt-in real DSH test with synthetic MySQL evidence; never connects to a database."""

import json
import sys
from functools import wraps
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dsh.plugins.metadata_tools.capabilities import Capabilities
from dsh.plugins.metadata_tools.server import build_server
from metadata_service import runner
from metadata_service.config import ROOT, get_settings
from metadata_service.store import Store, uid


class FixtureDatabase:
    def objects(self):
        return [
            {"name": "users", "kind": "table"},
            {"name": "orders", "kind": "table"},
            {"name": "v_users", "kind": "view"},
        ]

    def describe(self, names):
        tables = [
            {
                "name": "users",
                "kind": "table",
                "comment": "Synthetic users",
                "columns": [{"name": "id", "type": "bigint"}]
                + [{"name": f"field_{i}", "type": "varchar(80)"} for i in range(1, 40)],
                "keys": [{"name": "PRIMARY", "kind": "primary", "columns": ["id"]}],
            },
            {
                "name": "orders",
                "kind": "table",
                "comment": "Synthetic orders",
                "columns": [{"name": "id", "type": "bigint"}, {"name": "user_id", "type": "bigint"}]
                + [{"name": f"field_{i}", "type": "varchar(80)"} for i in range(2, 40)],
                "keys": [
                    {"name": "PRIMARY", "kind": "primary", "columns": ["id"]},
                    {"name": "fk_user", "kind": "foreign", "columns": ["user_id"]},
                ],
            },
            {
                "name": "v_users",
                "kind": "view",
                "columns": [{"name": "id", "type": "bigint"}, {"name": "name", "type": "varchar(80)"}],
                "keys": [],
            },
        ]
        fk = {
            "table": "orders",
            "name": "fk_user",
            "constrained_columns": ["user_id"],
            "referred_schema": "fixture",
            "referred_table": "users",
            "referred_columns": ["id"],
        }
        return (
            [table for table in tables if table["name"] in names],
            [fk] if "orders" in names else [],
            {"v_users": "CREATE VIEW v_users AS SELECT id, field_1 AS name FROM users"} if "v_users" in names else {},
            [],
        )

    def sample(self, *_args):
        return []

    def close(self):
        pass


def fixture_server(job_id):
    store = Store(get_settings())
    Capabilities.mysql = lambda _self: FixtureDatabase()
    original = Capabilities.save_findings

    @wraps(original)
    def record(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if kwargs.get("schema_refs"):
            self.store.update(job_id, fixture_refs_used=True)
        return result

    Capabilities.save_findings = record
    build_server(store, job_id).run(transport="stdio")


def main():
    settings = get_settings()
    settings.data_dir = ROOT / "data" / ("batch-smoke-" + uid())
    settings.initialize()
    store = Store(settings)
    source = store.create("source", {"type": "mysql", "name": "synthetic", "config": {"database": "fixture"}})
    job_id = store.new_job(source, tables=["users", "orders", "v_users"], allow_samples=False)
    store.claim(job_id)
    prepare = runner.prepare_runtime

    def prepare_fixture(store, job):
        home, workspace, env = prepare(store, job)
        patch = home / "profiles/sdk/cordis.patch.yml"
        value = json.loads(patch.read_text(encoding="utf-8"))
        value[-1]["insert"][0]["config"]["args"] = [str(Path(__file__).resolve()), "--mcp", job]
        patch.write_text(json.dumps(value), encoding="utf-8")
        return home, workspace, env

    runner.prepare_runtime = prepare_fixture
    runner.run_task(store, job_id)
    job = store.get(job_id)
    assert job["snapshot_id"] and job.get("fixture_refs_used"), (job["status"], job["error"], settings.data_dir)
    findings = store.get(job["snapshot_id"])["findings"]
    assert len(findings["tables"]) == 3 and sum(len(table["columns"]) for table in findings["tables"]) == 82
    assert any(relation["origin"] == "constraint" for relation in findings["relationships"])
    assert any(edge["source_table"] == "users" and edge["target_table"] == "v_users" for edge in findings["lineage"])
    print("LIVE_DSH_SCHEMA_REFERENCES_OK", settings.data_dir)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        fixture_server(sys.argv[2])
    else:
        main()
