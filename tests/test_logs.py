import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from dsh.plugins.metadata_tools.server import logged_tool
from metadata_service.progress import DSHProgress


def test_task_logs_cursor_scope_validation_and_restart(client, store):
    first = store.new_job()
    other = store.new_job()
    store.log(first, "task.queued", "等待运行")
    store.log(other, "task.queued", "另一个任务")
    store.log(first, "runtime.start", "正在启动")
    page = client.get(f"/api/v1/analyses/{first}/logs?limit=1").json()
    assert len(page["events"]) == 1 and page["has_more"]
    second = client.get(f"/api/v1/analyses/{first}/logs?after={page['next_cursor']}").json()
    assert [event["type"] for event in second["events"]] == ["runtime.start"]
    assert not second["has_more"]
    assert all(event["job_id"] == first for event in second["events"])
    assert client.get(f"/api/v1/analyses/{first}/logs?after=-1").status_code == 400
    assert client.get(f"/api/v1/analyses/{first}/logs?limit=501").status_code == 400
    assert client.get("/api/v1/analyses/missing/logs").status_code == 404
    restored = type(store)(store.settings)
    assert restored.logs(first)["next_cursor"] == second["next_cursor"]


def test_concurrent_logs_and_terminal_stage_protection(store):
    job_id = store.new_job()
    store.claim(job_id)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: store.log(job_id, "tool.completed", "完成"), range(12)))
    events = store.logs(job_id)["events"]
    assert len(events) == 12 and len({e["seq"] for e in events}) == 12
    store.update(job_id, status="cancelled", stage="已取消")
    store.log(job_id, "tool.completed", "晚到的工具事件", stage="工具完成")
    assert store.get(job_id)["stage"] == "已取消"


def test_tool_and_sdk_logs_do_not_capture_arguments_results_or_errors(store, capsys):
    job_id = store.new_job()
    store.claim(job_id)
    secret = "synthetic-secret-not-for-logs"

    def tool(password: str):
        return {"password": password, "rows": [secret]}

    wrapped = logged_tool(store, job_id, "sample_rows", tool)
    assert wrapped(secret)["password"] == secret

    def failing(password: str):
        raise ValueError("connection password=" + password)

    with pytest.raises(ValueError):
        logged_tool(store, job_id, "describe_tables", failing)(secret)
    progress = DSHProgress(store, job_id)
    progress({"type": "request/header", "data": {"header": {"messages": [secret]}}})
    progress({"type": "assistant/message", "data": {"content": secret}})
    progress({"type": "turn/end", "data": {"reason": {"kind": "completed", "message": secret}}})
    page = store.logs(job_id)
    assert secret not in json.dumps(page)
    assert secret not in capsys.readouterr().err
    assert [e["type"] for e in page["events"]] == [
        "tool.started",
        "tool.completed",
        "tool.started",
        "tool.failed",
        "model.request",
        "model.response",
        "dsh.turn_end",
    ]
    assert page["events"][3]["code"] == "ValueError"
    assert page["events"][1]["duration_ms"] >= 0


def test_retention_is_bounded_per_task(store):
    job_id = store.new_job()
    other = store.new_job()
    with store.engine.begin() as connection:
        connection.execute(
            store.events.insert(),
            [
                {
                    "job_id": job_id,
                    "time": "2026-10-10T00:00:00+00:00",
                    "payload": {"type": "fixture", "message": "test"},
                }
                for _ in range(5002)
            ],
        )
    store.log(other, "task.queued", "其他任务")
    store.log(job_id, "task.finished", "结束")
    with store.engine.connect() as connection:
        from sqlalchemy import func, select

        count = connection.execute(
            select(func.count()).select_from(store.events).where(store.events.c.job_id == job_id)
        ).scalar_one()
    assert count == 5000
    assert store.logs(job_id, after=1)["truncated"]
    assert len(store.logs(other)["events"]) == 1
