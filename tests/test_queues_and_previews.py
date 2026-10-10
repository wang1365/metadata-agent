from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Lock

import pytest
from sqlalchemy.exc import OperationalError
from test_service import prepared, upload_workbook

from dsh.plugins.metadata_tools.capabilities import Capabilities
from dsh.plugins.metadata_tools.mysql import MySQL
from dsh.plugins.metadata_tools.server import logged_tool
from metadata_service.config import Settings
from metadata_service.worker import recover_queue


def test_atomic_claims_and_separate_queue_recovery(store):
    analysis = [store.new_job() for _ in range(12)]
    qa = [store.new_job(question="synthetic", conversation_id=str(i)) for i in range(12)]

    def claim(queue):
        result = []
        while (job := store.claim_next(queue)) is not None:
            result.append(job["id"])
        return result

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, ["analysis"] * 4 + ["qa"] * 4))
    ids = [job for jobs in results for job in jobs]
    assert len(ids) == len(set(ids)) == 24 and set(ids) == set(analysis + qa)
    recover_queue(store, "analysis")
    assert all(store.get(job)["status"] == "failed" for job in analysis)
    assert all(store.get(job)["status"] == "running" for job in qa)


def test_same_conversation_is_serial_but_other_conversations_can_run(store):
    first = store.new_job(question="first", conversation_id="one")
    second = store.new_job(question="second", conversation_id="one")
    other = store.new_job(question="other", conversation_id="two")
    assert store.claim_next("qa")["id"] == first
    assert store.claim_next("qa")["id"] == other
    assert store.claim_next("qa") is None
    store.update(first, status="succeeded")
    assert store.claim_next("qa")["id"] == second


def test_configured_slots_dispatch_both_queues_concurrently(store, monkeypatch):
    from metadata_service.worker import work

    store.settings.analysis_concurrency = 3
    store.settings.qa_concurrency = 1
    ids = [store.new_job() for _ in range(3)] + [store.new_job(question="synthetic", conversation_id="one")]
    barrier = Barrier(4)
    lock = Lock()
    active = {"analysis": 0, "qa": 0}
    peaks = dict(active)

    class Process:
        def __init__(self, args, **_kwargs):
            self.job_id = args[-1]
            self.queue = store.get(self.job_id)["queue"]
            self.polled = False
            self.finished = False
            with lock:
                active[self.queue] += 1
                peaks[self.queue] = max(peaks[self.queue], active[self.queue])
            barrier.wait(timeout=8)

        def poll(self):
            if self.finished:
                return 0
            if not self.polled:
                self.polled = True
                return None
            store.update(self.job_id, status="succeeded")
            with lock:
                active[self.queue] -= 1
            self.finished = True
            return 0

    monkeypatch.setattr("metadata_service.worker.subprocess.Popen", Process)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work, store, True, queue) for queue in ("analysis", "qa")]
        for future in futures:
            future.result(timeout=12)
    assert peaks == {"analysis": 3, "qa": 1}
    assert all(store.get(job)["status"] == "succeeded" for job in ids)


def test_queue_config_and_history_include_unanswered_questions(client, store, tmp_path, monkeypatch):
    monkeypatch.setenv("METADATA_ANALYSIS_CONCURRENCY", "3")
    monkeypatch.setenv("METADATA_QA_CONCURRENCY", "2")
    settings = Settings(data_dir=tmp_path / "settings").initialize()
    assert (settings.analysis_concurrency, settings.qa_concurrency) == (3, 2)
    monkeypatch.setenv("METADATA_QA_CONCURRENCY", "0")
    with pytest.raises(ValueError, match="CONCURRENCY"):
        Settings(data_dir=tmp_path / "invalid").initialize()
    job, tools, findings = prepared(client, store)
    tools.save_dictionary_draft(findings)
    store.update(job, confirmed=True)
    snap = tools.finalize_snapshot()["snapshot_id"]
    first = client.post(f"/api/v1/snapshots/{snap}/questions", json={"question": "first question"}).json()
    second = client.post(
        f"/api/v1/snapshots/{snap}/questions",
        json={"question": "second question", "conversation_id": first["conversation_id"]},
    ).json()
    store.update(first["task_id"], status="failed", error="DSH_MODEL_ERROR")
    history = client.get("/api/v1/conversations", params={"snapshot_id": snap}).json()
    assert history["total"] == 1 and history["conversations"][0]["task_count"] == 2
    assert history["conversations"][0]["title"] == "first question"
    detail = client.get("/api/v1/conversations/" + first["conversation_id"]).json()
    assert [task["id"] for task in detail["tasks"]] == [first["task_id"], second["task_id"]]
    assert detail["answers"] == [] and "draft" not in str(detail["tasks"])
    assert all(job["question"] for job in client.get("/api/v1/analyses?queue=qa").json())
    assert all(not job.get("question") for job in client.get("/api/v1/analyses?queue=analysis").json())


def test_previews_read_raw_evidence_without_jobs_or_model(client, store, monkeypatch):
    excel = upload_workbook(client)
    preview = client.get(f"/api/v1/sources/{excel}/preview", params={"name": "字典", "region": "A1:C3"})
    assert preview.status_code == 200 and preview.json()["cells"]
    assert client.get(f"/api/v1/sources/{excel}/preview?region=A1:ZZ9999").status_code == 400
    source = client.post(
        "/api/v1/sources/mysql",
        json={
            "name": "fixture",
            "host": "placeholder",
            "database": "fixture",
            "username": "reader",
            "password": "do-not-return",
        },
    ).json()["id"]

    class Database:
        samples = 0

        def __init__(self, _config):
            pass

        def describe(self, names):
            if names != ["users"]:
                raise ValueError("UNKNOWN_TABLE")
            return [{"name": "users", "columns": [{"name": "id", "type": "bigint"}]}], [], {}, []

        def sample(self, table, columns, limit):
            type(self).samples += 1
            return [{"id": "x" * 500} for _ in range(limit)]

        def close(self):
            pass

    monkeypatch.setattr("metadata_service.api.MySQL", Database)
    assert "password" not in client.get(f"/api/v1/sources/{source}").text
    assert client.get(f"/api/v1/sources/{source}/preview?name=users").status_code == 200 and Database.samples == 0
    rows = client.post(
        f"/api/v1/sources/{source}/sample", json={"table": "users", "columns": ["id"], "limit": 20}
    ).json()
    assert len(rows["rows"]) == 20 and len(rows["rows"][0]["id"]) == 200 and rows["sent_to_model"] is False
    assert (
        client.post(f"/api/v1/sources/{source}/sample", json={"table": "users", "columns": ["missing"]}).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/v1/sources/{source}/sample", json={"table": "users", "columns": ["id"], "limit": 21}
        ).status_code
        == 422
    )
    assert store.list("job") == []


def test_collation_conflict_returns_warning_without_fake_counts(store, monkeypatch):
    class DBError(Exception):
        pass

    class Connection:
        def execute(self, *_args):
            raise OperationalError("private SQL", {}, DBError(1267, "private data"))

    database = MySQL.__new__(MySQL)
    database.quote = lambda name: name

    @contextmanager
    def connection():
        yield Connection()

    database.connection = connection
    result = database.validate("a", ["id"], "b", ["id"])
    assert result["skipped"] and result["reason"] == "COLLATION_MISMATCH"
    assert not {"sampled", "matched"} & set(result) and "private" not in str(result)
    job = store.new_job()
    store.claim(job)
    tools = Capabilities(store, job)
    monkeypatch.setattr(tools, "check_columns", lambda *_: {"keys": [{"kind": "primary", "columns": ["id"]}]})
    monkeypatch.setattr(database, "close", lambda: None)
    monkeypatch.setattr(tools, "mysql", lambda: database)
    output = logged_tool(store, job, "validate_relationship", tools.validate_relationship)("a", ["id"], "b", ["id"])
    assert output["skipped"] and output["validation"] == result and output["evidence_ids"]
    event = store.logs(job)["events"][-1]
    assert event["level"] == "warning" and event["code"] == "COLLATION_MISMATCH"
