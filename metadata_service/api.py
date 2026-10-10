import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from dsh.plugins.metadata_tools.excel import inspect_workbook
from dsh.plugins.metadata_tools.mysql import MySQL

from .config import ROOT, get_settings
from .models import AnalysisRequest, MappingRequest, MySQLSource, QuestionRequest, ReviewRequest
from .store import TERMINAL, Store, now, uid


def public_source(source):
    return {key: value for key, value in source.items() if key not in ("password", "path")}


def create_app(settings=None):
    settings = settings or get_settings()
    store = Store(settings)
    app = FastAPI(
        title="Metadata Service",
        version="0.1.0",
        description="DSH-powered metadata analysis. Local single-user preview.",
    )
    app.state.store = store
    prefix = "/api/v1"

    @app.exception_handler(KeyError)
    def missing(_request, _exc):
        return JSONResponse(status_code=404, content={"detail": "NOT_FOUND"})

    @app.exception_handler(ValueError)
    def invalid(_request, exc):
        # Internal exceptions never include connection strings or credentials.
        message = str(exc)
        if len(message) > 200:
            message = "INVALID_INPUT"
        return JSONResponse(status_code=400, content={"detail": message})

    @app.exception_handler(RequestValidationError)
    def validation(_request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(error["loc"]), "type": error["type"], "msg": error["msg"]} for error in exc.errors()
                ]
            },
        )

    @app.get(prefix + "/health")
    def health():
        return {"status": "ok", "runtime": "deepseek-harness", "worker": "external", "model": settings.model}

    @app.get(prefix + "/sources")
    def sources():
        return [public_source(source) for source in store.list("source")]

    @app.post(prefix + "/sources/excel", status_code=201)
    def upload(file: UploadFile):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".xlsx", ".xls"):
            raise ValueError("UNSUPPORTED_FILE_TYPE")
        source_id = uid()
        folder = settings.data_dir / "uploads"
        folder.mkdir(exist_ok=True)
        path = folder / (source_id + suffix)
        try:
            size = 0
            with path.open("xb") as output:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_file_bytes:
                        raise HTTPException(413, "FILE_LIMIT_EXCEEDED")
                    output.write(chunk)
            sheets = inspect_workbook(path)
        except BaseException as exc:
            path.unlink(missing_ok=True)
            if isinstance(exc, (HTTPException, ValueError)):
                raise
            raise ValueError("INVALID_OR_ENCRYPTED_WORKBOOK") from None
        finally:
            file.file.close()
        store.create(
            "source",
            {
                "type": "excel",
                "name": Path(file.filename or "dictionary").name,
                "path": str(path),
                "sheets": sheets,
                "size": size,
            },
            source_id,
        )
        return public_source(store.get(source_id, "source"))

    @app.post(prefix + "/sources/mysql", status_code=201)
    def mysql_source(body: MySQLSource):
        config = body.model_dump(exclude={"password", "name"})
        source_id = store.create(
            "source",
            {
                "type": "mysql",
                "name": body.name,
                "config": config,
                "password": settings.cipher.encrypt(body.password.encode()).decode(),
            },
        )
        return public_source(store.get(source_id, "source"))

    def database(source_id):
        source = store.get(source_id, "source")
        if source["type"] != "mysql":
            raise ValueError("MYSQL_SOURCE_REQUIRED")
        config = dict(source["config"])
        config["password"] = settings.cipher.decrypt(source["password"].encode()).decode()
        return MySQL(config)

    @app.post(prefix + "/sources/{source_id}/test")
    def test_connection(source_id: str):
        db = database(source_id)
        try:
            return db.test()
        except Exception:
            raise HTTPException(400, "MYSQL_CONNECTION_FAILED: check connection and read privileges") from None
        finally:
            db.close()

    @app.get(prefix + "/sources/{source_id}/objects")
    def objects(source_id: str, offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("INVALID_PAGINATION")
        source = store.get(source_id, "source")
        if source["type"] == "excel":
            return {"objects": source["sheets"]}
        db = database(source_id)
        try:
            items = db.objects()
            return {"objects": items[offset : offset + limit], "total": len(items)}
        except Exception:
            raise HTTPException(400, "MYSQL_OBJECT_LIST_FAILED") from None
        finally:
            db.close()

    @app.post(prefix + "/analyses", status_code=202)
    def start_analysis(body: AnalysisRequest):
        job_id = store.new_job(**body.model_dump())
        store.log(job_id, "task.queued", "任务已入队，等待 Worker")
        return {"task_id": job_id}

    @app.get(prefix + "/analyses/{job_id}/logs")
    def task_logs(job_id: str, after: int = 0, limit: int = 200):
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError("INVALID_PAGINATION")
        return store.logs(job_id, after, limit)

    @app.get(prefix + "/analyses")
    def jobs():
        return [
            {key: value for key, value in job.items() if key not in ("evidence", "draft")} for job in store.list("job")
        ]

    @app.get(prefix + "/analyses/{job_id}")
    def job(job_id: str):
        return {key: value for key, value in store.get(job_id, "job").items() if key not in ("evidence", "draft")}

    @app.get(prefix + "/analyses/{job_id}/preview")
    def preview(job_id: str):
        task = store.get(job_id, "job")
        return {"version": task["version"], "findings": task["draft"], "evidence": task["evidence"]}

    @app.put(prefix + "/analyses/{job_id}/mapping")
    def mapping(job_id: str, body: MappingRequest):
        def update(payload):
            if payload["status"] != "awaiting_confirmation" or payload["version"] != body.version:
                raise HTTPException(409, "STALE_OR_NOT_EDITABLE_MAPPING")
            store.check_evidence(body.findings, payload["evidence"])
            payload.update(draft=body.findings.model_dump(), version=payload["version"] + 1)

        result = store.mutate(job_id, update)
        return {"version": result["version"]}

    @app.post(prefix + "/analyses/{job_id}/confirm", status_code=202)
    def confirm(job_id: str):
        def apply(payload):
            if payload["confirmed"]:
                return
            if payload["status"] != "awaiting_confirmation" or not payload["draft"]["tables"]:
                raise HTTPException(409, "MAPPING_NOT_READY")
            payload.update(confirmed=True, status="queued", stage="等待继续分析")

        store.mutate(job_id, apply)
        store.log(job_id, "task.confirmed", "人工确认已接收，等待继续分析")
        return {"task_id": job_id}

    @app.post(prefix + "/analyses/{job_id}/cancel", status_code=202)
    def cancel(job_id: str):
        def apply(payload):
            if payload["status"] not in TERMINAL:
                payload["cancel_requested"] = True
                if payload["status"] in ("queued", "awaiting_confirmation"):
                    payload.update(status="cancelled", stage="已取消")

        result = store.mutate(job_id, apply)
        store.log(job_id, "task.cancel_requested", "已收到取消请求", level="warning")
        return {"task_id": job_id, "status": result["status"], "cancel_requested": result["cancel_requested"]}

    @app.post(prefix + "/analyses/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        previous = store.get(job_id, "job")
        if previous["status"] not in {"failed", "cancelled", "partial_success"}:
            raise HTTPException(409, "TASK_NOT_RETRYABLE")
        keys = [
            "tables",
            "allow_samples",
            "confirmed",
            "draft",
            "version",
            "evidence",
            "queries",
            "sample_rows",
            "question",
            "bound_snapshot_id",
            "conversation_id",
        ]
        extra = {key: previous[key] for key in keys if key in previous}
        retry_id = store.new_job(previous["source_id"], retry_of=job_id, **extra)
        store.log(retry_id, "task.queued", "重试任务已入队，等待 Worker")
        return {"task_id": retry_id}

    @app.get(prefix + "/snapshots")
    def snapshots():
        return [
            {
                "id": snap["id"],
                "created": snap["created"],
                "source_id": snap["source_id"],
                "tables": len(snap["findings"]["tables"]),
            }
            for snap in store.list("snapshot")
        ]

    @app.get(prefix + "/snapshots/{snapshot_id}/tables")
    def tables(snapshot_id: str, query: str = "", offset: int = 0, limit: int = 50):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("INVALID_PAGINATION")
        snap = store.get(snapshot_id, "snapshot")
        rows = [
            table
            for table in snap["findings"]["tables"]
            if query.lower() in json.dumps(table, ensure_ascii=False).lower()
        ]
        return {"tables": rows[offset : offset + limit], "total": len(rows), "warnings": snap["findings"]["warnings"]}

    @app.get(prefix + "/snapshots/{snapshot_id}/graph")
    def graph(snapshot_id: str, kind: str = "er", root: str | None = None):
        if kind not in ("er", "lineage"):
            raise ValueError("INVALID_GRAPH_KIND")
        findings = store.get(snapshot_id, "snapshot")["findings"]
        root = root or (findings["tables"][0]["name"] if findings["tables"] else None)
        edges = [
            edge
            for edge in findings["relationships" if kind == "er" else "lineage"]
            if root in (edge["source_table"], edge["target_table"])
        ][:200]
        names = {root} | {edge[key] for edge in edges for key in ("source_table", "target_table")}
        return {
            "tables": [table for table in findings["tables"] if table["name"] in names],
            "edges": edges,
            "root": root,
        }

    @app.get(prefix + "/snapshots/{snapshot_id}/evidence/{evidence_id}")
    def evidence(snapshot_id: str, evidence_id: str):
        return store.get(snapshot_id, "snapshot")["evidence"][evidence_id]

    @app.patch(prefix + "/relationships/{relationship_id}")
    def review(relationship_id: str, snapshot_id: str, body: ReviewRequest):
        def apply(payload):
            relation = next((r for r in payload["findings"]["relationships"] if r["id"] == relationship_id), None)
            if not relation:
                raise KeyError("NOT_FOUND")
            relation["review"] = body.status
            payload["reviews"].append(
                {"relationship_id": relationship_id, "status": body.status, "note": body.note, "time": now()}
            )

        store.mutate(snapshot_id, apply)
        return {"status": body.status}

    @app.post(prefix + "/snapshots/{snapshot_id}/questions", status_code=202)
    def question(snapshot_id: str, body: QuestionRequest):
        store.get(snapshot_id, "snapshot")
        conversation_id = body.conversation_id
        if conversation_id:
            if store.get(conversation_id, "conversation")["snapshot_id"] != snapshot_id:
                raise ValueError("CONVERSATION_SNAPSHOT_MISMATCH")
        else:
            conversation_id = store.create("conversation", {"snapshot_id": snapshot_id})
        job_id = store.new_job(question=body.question, bound_snapshot_id=snapshot_id, conversation_id=conversation_id)
        store.log(job_id, "task.queued", "问答任务已入队，等待 Worker")
        return {"task_id": job_id, "conversation_id": conversation_id}

    @app.get(prefix + "/conversations/{conversation_id}")
    def conversation(conversation_id: str):
        info = store.get(conversation_id, "conversation")
        info["answers"] = sorted(
            [answer for answer in store.list("answer") if answer["conversation_id"] == conversation_id],
            key=lambda a: a["created"],
        )
        return info

    @app.get(prefix + "/snapshots/{snapshot_id}/exports/{format}")
    def export(snapshot_id: str, format: str):
        snap = store.get(snapshot_id, "snapshot")
        if format == "json":
            # Samples are never persisted in snapshots.
            return Response(
                json.dumps(snap, ensure_ascii=False, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="metadata.json"'},
            )
        if format != "csv":
            raise ValueError("UNSUPPORTED_EXPORT_FORMAT")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["snapshot_id", "table", "column", "type", "comment", "evidence_ids"])

        def safe(value):
            value = str(value)
            return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value

        for table in snap["findings"]["tables"]:
            for column in table["columns"]:
                writer.writerow(
                    [
                        snapshot_id,
                        safe(table["name"]),
                        safe(column["name"]),
                        safe(column["type"]),
                        safe(column["comment"]),
                        "|".join(column["evidence_ids"]),
                    ]
                )
        return Response(
            "\ufeff" + output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="metadata.csv"'},
        )

    dist = ROOT / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    return app
