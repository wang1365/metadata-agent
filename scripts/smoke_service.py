"""Opt-in real DSH/Bailian smoke test; never touches a business database."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import openpyxl
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metadata_service.api import create_app
from metadata_service.config import ROOT, Settings
from metadata_service.store import uid


def worker(settings):
    env = os.environ.copy()
    env.update(METADATA_DATA_DIR=str(settings.data_dir), PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    subprocess.run(
        [sys.executable, "-m", "metadata_service.worker", "--once"], env=env, cwd=ROOT, check=True, timeout=240
    )


def main():
    settings = Settings(data_dir=ROOT / "data" / ("smoke-" + uid())).initialize()
    app = create_app(settings)
    with TestClient(app) as client:
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "users"
        sheet.append(["表名", "字段名", "类型", "主键", "说明"])
        sheet.append(["users", "id", "bigint", "是", "用户 ID"])
        sheet.append(["users", "name", "varchar(100)", "否", "用户名称"])
        data = io.BytesIO()
        book.save(data)
        source = client.post("/api/v1/sources/excel", files={"file": ("dictionary.xlsx", data.getvalue())})
        assert source.status_code == 201, source.text
        job_id = client.post("/api/v1/analyses", json={"source_id": source.json()["id"]}).json()["task_id"]
        worker(settings)
        job = client.get(f"/api/v1/analyses/{job_id}").json()
        print(json.dumps(job, ensure_ascii=True))
        assert job["status"] == "awaiting_confirmation", str(settings.data_dir / "jobs" / job_id / "runner.log")
        assert client.post(f"/api/v1/analyses/{job_id}/confirm").status_code == 202
        worker(settings)
        job = client.get(f"/api/v1/analyses/{job_id}").json()
        print(json.dumps(job, ensure_ascii=True))
        assert job["status"] in ("succeeded", "partial_success"), str(
            settings.data_dir / "jobs" / job_id / "runner.log"
        )
        snapshot_id = job["snapshot_id"]
        qa = client.post(
            f"/api/v1/snapshots/{snapshot_id}/questions", json={"question": "users 表有哪些字段？请引用数据字典证据。"}
        ).json()
        worker(settings)
        job = client.get("/api/v1/analyses/" + qa["task_id"]).json()
        print(json.dumps(job, ensure_ascii=True))
        assert job["status"] == "succeeded", str(settings.data_dir / "jobs" / qa["task_id"] / "runner.log")
        assert not any("mysql" in name for name in job["observed_tools"])
        events = client.get(f"/api/v1/analyses/{qa['task_id']}/logs").json()["events"]
        event_types = {event["type"] for event in events}
        assert {
            "mcp.ready",
            "model.request",
            "model.response",
            "tool.started",
            "tool.completed",
            "task.finished",
        } <= event_types
        assert events[-1]["type"] == "task.finished"
        print("LIVE_DSH_EXCEL_AND_QA_OK", settings.data_dir)


if __name__ == "__main__":
    main()
