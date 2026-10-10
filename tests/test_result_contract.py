from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from dsh.plugins.metadata_tools.capabilities import Capabilities
from dsh.plugins.metadata_tools.server import build_server
from metadata_service.progress import DSHProgress
from metadata_service.runner import run_task


@pytest.mark.anyio
async def test_mcp_exposes_strict_findings_schema(store):
    source = store.create("source", {"type": "mysql"})
    job = store.new_job(source)
    store.claim(job)
    tools = {tool.name: tool for tool in await build_server(store, job).list_tools()}
    schema = tools["save_findings"].inputSchema
    model = schema["$defs"]["FindingsInput"]
    assert model["additionalProperties"] is False
    assert set(model["properties"]) == {"tables", "relationships", "lineage", "warnings"}
    assert "mode" in schema["properties"]


def test_result_batches_preserve_prior_tables_and_allow_edges_to_existing_objects(store):
    source = store.create("source", {"type": "mysql"})
    job = store.new_job(source)
    store.claim(job)
    tools = Capabilities(store, job)
    ref = store.evidence(job, "synthetic", {}, {})
    for name in ("users", "view_users"):
        tools.save_findings(
            {"tables": [{"name": name, "columns": [{"name": "id"}], "evidence_ids": [ref]}]}, mode="merge"
        )
    edge = {
        "source_table": "users",
        "target_table": "view_users",
        "source_column": "id",
        "target_column": "id",
        "evidence_ids": [ref],
    }
    tools.save_findings({"lineage": [edge]}, mode="merge")
    tools.save_findings({"lineage": [edge]}, mode="merge")
    draft = store.get(job)["draft"]
    assert len(draft["tables"]) == 2 and len(draft["lineage"]) == 1
    with pytest.raises(ValueError):
        tools.save_findings({"source": "invented", "scope": "invented"}, mode="merge")


def test_parallel_batches_do_not_drop_tables(store):
    source = store.create("source", {"type": "mysql"})
    job = store.new_job(source)
    store.claim(job)
    tools = Capabilities(store, job)
    ref = store.evidence(job, "synthetic", {}, {})

    def save(index):
        tools.save_findings(
            {"tables": [{"name": f"table_{index}", "columns": [{"name": "id"}], "evidence_ids": [ref]}]}, mode="merge"
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(save, range(8)))
    assert len(store.get(job)["draft"]["tables"]) == 8


def test_sdk_reports_tool_schema_error_before_tool_body_without_payload(store):
    job = store.new_job()
    store.claim(job)
    progress = DSHProgress(store, job)
    progress({"type": "request/header", "data": {"header": {"tools": [{"name": "mcp__metadata__save_findings"}]}}})
    progress(
        {
            "type": "tool/call",
            "data": {"name": "mcp__metadata__save_findings", "callId": "call", "arguments": "secret-arguments"},
        }
    )
    progress(
        {
            "type": "tool/result",
            "data": {
                "message": {
                    "content": [
                        {"type": "tool-result", "toolCallId": "call", "isError": True, "content": "secret-error-input"}
                    ]
                }
            },
        }
    )
    event = store.logs(job)["events"][-1]
    assert event["code"] == "DSH_TOOL_ERROR" and event["tool"] == "mcp__metadata__save_findings"
    assert "secret" not in str(event)


@pytest.mark.parametrize(
    "reason,code", [("max-tokens", "DSH_MAX_TOKENS"), ("error", "DSH_MODEL_ERROR"), (None, "DSH_NO_TURN_END")]
)
def test_runner_preserves_finish_reason_without_publishing(store, monkeypatch, reason, code):
    source = store.create("source", {"type": "mysql"})
    job = store.new_job(source)
    store.claim(job)
    monkeypatch.setattr(
        "metadata_service.runner.prepare_runtime", lambda *_: (store.settings.data_dir, store.settings.data_dir, {})
    )

    class Harness:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, *args, **kwargs):
            return SimpleNamespace(finish_reason=reason)

    monkeypatch.setattr("metadata_service.runner.DeepSeekHarness", Harness)
    run_task(store, job)
    result = store.get(job)
    assert result["status"] == "failed" and result["error"] == code
    assert result["snapshot_id"] is None
    progress = DSHProgress(store, job)
    progress({"type": "turn/end", "data": {"reason": {"kind": reason, "message": "untrusted payload"}}})
    event = store.logs(job)["events"][-1]
    assert event["code"] == code and event["level"] == "error"
    assert "untrusted payload" not in str(event)


@pytest.fixture
def anyio_backend():
    return "asyncio"
