import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from dsh.plugins.metadata_tools.excel import inspect_workbook, preview_region, read_region
from dsh.plugins.metadata_tools.mysql import MySQL, filter_objects

from .api_docs import INTRODUCTION, PREFIX, TAGS, install_docs, operation
from .config import ROOT, get_settings
from .models import AnalysisRequest, MappingRequest, MySQLSource, QuestionRequest, ReviewRequest, SamplePreviewRequest
from .store import TERMINAL, Store, job_queue, now, uid


def public_source(source):
    return {key: value for key, value in source.items() if key not in ("password", "path")}


def create_app(settings=None):
    settings = settings or get_settings()
    store = Store(settings)
    app = FastAPI(
        title="Metadata Service",
        version="0.1.0",
        description=INTRODUCTION,
        docs_url=None,
        redoc_url=None,
        openapi_url=PREFIX + "/openapi.json",
        openapi_tags=TAGS,
    )
    app.state.store = store
    prefix = PREFIX
    install_docs(app)

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

    @app.get(prefix + "/health", **operation("health"))
    def health():
        return {
            "status": "ok",
            "runtime": "deepseek-harness",
            "worker": "external",
            "model": settings.model,
            "queues": {"analysis": settings.analysis_concurrency, "qa": settings.qa_concurrency},
            "display_timezone": "Asia/Shanghai",
        }

    @app.get(prefix + "/sources", **operation("sources"))
    def sources():
        return [public_source(source) for source in store.list("source")]

    @app.post(prefix + "/sources/excel", status_code=201, **operation("upload", 201))
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

    @app.post(prefix + "/sources/mysql", status_code=201, **operation("mysql_source", 201))
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

    @app.post(prefix + "/sources/{source_id}/test", **operation("test_connection"))
    def test_connection(source_id: str):
        db = database(source_id)
        try:
            return db.test()
        except Exception:
            raise HTTPException(400, "MYSQL_CONNECTION_FAILED: check connection and read privileges") from None
        finally:
            db.close()

    @app.get(prefix + "/sources/{source_id}/objects", **operation("objects"))
    def objects(source_id: str, offset: int = 0, limit: int = 100, query: str = "", match: str = "fuzzy"):
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("INVALID_PAGINATION")
        source = store.get(source_id, "source")
        if source["type"] == "excel":
            return {"objects": source["sheets"]}
        filter_objects([], query, match)  # Validate before opening the database connection.
        db = database(source_id)
        try:
            items = sorted(db.objects(), key=lambda item: item["name"])
            filtered = filter_objects(items, query, match)
            return {"objects": filtered[offset : offset + limit], "total": len(filtered), "source_total": len(items)}
        except ValueError:
            raise
        except Exception:
            raise HTTPException(400, "MYSQL_OBJECT_LIST_FAILED") from None
        finally:
            db.close()

    @app.post(prefix + "/analyses", status_code=202, **operation("start_analysis", 202))
    def start_analysis(body: AnalysisRequest):
        source = store.get(body.source_id, "source")
        if source["type"] == "mysql":
            if not body.tables:
                raise ValueError("TABLE_SELECTION_REQUIRED")
            if len(set(body.tables)) != len(body.tables):
                raise ValueError("DUPLICATE_TABLE_SELECTION")
            db = database(body.source_id)
            try:
                available = {item["name"] for item in db.objects()}
            except Exception:
                raise HTTPException(400, "MYSQL_OBJECT_LIST_FAILED") from None
            finally:
                db.close()
            if not set(body.tables) <= available:
                raise ValueError("UNKNOWN_TABLE_SELECTION: refresh objects and select again")
        elif body.tables:
            raise ValueError("TABLE_SELECTION_MYSQL_ONLY")
        job_id = store.new_job(**body.model_dump())
        store.log(job_id, "task.queued", "任务已入队，等待 Worker")
        return {"task_id": job_id}

    @app.get(prefix + "/sources/{source_id}", **operation("source_detail"))
    def source_detail(source_id: str):
        return public_source(store.get(source_id, "source"))

    @app.get(prefix + "/sources/{source_id}/preview", **operation("source_preview"))
    def source_preview(source_id: str, name: str = "", region: str = "A1:J20", formatted: bool = False):
        source = store.get(source_id, "source")
        if source["type"] == "excel":
            sheet = name or source["sheets"][0]["name"]
            if sheet not in {item["name"] for item in source["sheets"]}:
                raise ValueError("UNKNOWN_SHEET")
            if formatted:
                return preview_region(source["path"], sheet, region)
            return {"sheet": sheet, "region": region, "cells": read_region(source["path"], sheet, region)}
        if not name:
            raise ValueError("TABLE_NAME_REQUIRED")
        db = database(source_id)
        try:
            tables, keys, _, warnings = db.describe([name])
            return {"tables": tables, "foreign_keys": keys, "warnings": warnings}
        except ValueError:
            raise
        except Exception:
            raise HTTPException(400, "MYSQL_STRUCTURE_PREVIEW_FAILED") from None
        finally:
            db.close()

    @app.post(prefix + "/sources/{source_id}/sample", **operation("source_sample"))
    def source_sample(source_id: str, body: SamplePreviewRequest):
        db = database(source_id)
        try:
            tables, _, _, _ = db.describe([body.table])
            if len(set(body.columns)) != len(body.columns) or not set(body.columns) <= {
                column["name"] for column in tables[0]["columns"]
            }:
                raise ValueError("UNKNOWN_OR_DUPLICATE_COLUMN")
            rows = db.sample(body.table, body.columns, body.limit)
            return {
                "rows": [
                    {key: str(value)[:200] if value is not None else None for key, value in row.items()} for row in rows
                ],
                "method": "first_rows_not_random",
                "max_rows": body.limit,
                "max_value_chars": 200,
                "sent_to_model": False,
            }
        except ValueError:
            raise
        except Exception:
            raise HTTPException(400, "MYSQL_SAMPLE_PREVIEW_FAILED") from None
        finally:
            db.close()

    @app.get(prefix + "/analyses/{job_id}/logs", **operation("task_logs"))
    def task_logs(job_id: str, after: int = 0, limit: int = 200):
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError("INVALID_PAGINATION")
        return store.logs(job_id, after, limit)

    @app.get(prefix + "/analyses", **operation("jobs"))
    def jobs(queue: str = "all"):
        if queue not in ("all", "analysis", "qa"):
            raise ValueError("INVALID_QUEUE")
        return [
            {**{key: value for key, value in job.items() if key not in ("evidence", "draft")}, "queue": job_queue(job)}
            for job in store.list("job")
            if queue == "all" or job_queue(job) == queue
        ]

    @app.get(prefix + "/analyses/{job_id}", **operation("job"))
    def job(job_id: str):
        return {key: value for key, value in store.get(job_id, "job").items() if key not in ("evidence", "draft")}

    @app.get(prefix + "/analyses/{job_id}/preview", **operation("preview"))
    def preview(job_id: str):
        task = store.get(job_id, "job")
        return {"version": task["version"], "findings": task["draft"], "evidence": task["evidence"]}

    @app.put(prefix + "/analyses/{job_id}/mapping", **operation("mapping"))
    def mapping(job_id: str, body: MappingRequest):
        def update(payload):
            if payload["status"] != "awaiting_confirmation" or payload["version"] != body.version:
                raise HTTPException(409, "STALE_OR_NOT_EDITABLE_MAPPING")
            store.check_evidence(body.findings, payload["evidence"])
            payload.update(draft=body.findings.model_dump(), version=payload["version"] + 1)

        result = store.mutate(job_id, update)
        return {"version": result["version"]}

    @app.post(prefix + "/analyses/{job_id}/confirm", status_code=202, **operation("confirm", 202))
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

    @app.post(prefix + "/analyses/{job_id}/cancel", status_code=202, **operation("cancel", 202))
    def cancel(job_id: str):
        def apply(payload):
            if payload["status"] not in TERMINAL:
                payload["cancel_requested"] = True
                if payload["status"] in ("queued", "awaiting_confirmation"):
                    payload.update(status="cancelled", stage="已取消")

        result = store.mutate(job_id, apply)
        store.log(job_id, "task.cancel_requested", "已收到取消请求", level="warning")
        return {"task_id": job_id, "status": result["status"], "cancel_requested": result["cancel_requested"]}

    @app.post(prefix + "/analyses/{job_id}/retry", status_code=202, **operation("retry", 202))
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

    @app.get(prefix + "/snapshots", **operation("snapshots"))
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

    @app.get(prefix + "/snapshots/{snapshot_id}/tables", **operation("tables"))
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

    @app.get(prefix + "/snapshots/{snapshot_id}/graph", **operation("graph"))
    def graph(snapshot_id: str, kind: str = "er", root: str | None = None):
        if kind not in ("er", "lineage"):
            raise ValueError("INVALID_GRAPH_KIND")
        findings = store.get(snapshot_id, "snapshot")["findings"]
        if root is not None and root not in {table["name"] for table in findings["tables"]}:
            raise ValueError("UNKNOWN_GRAPH_ROOT")
        candidates = [
            edge
            for edge in findings["relationships" if kind == "er" else "lineage"]
            if root is None or root in (edge["source_table"], edge["target_table"])
        ]
        edges = candidates[:200]
        names = {root} | {edge[key] for edge in candidates for key in ("source_table", "target_table")}
        return {
            "tables": [table for table in findings["tables"] if root is None or table["name"] in names],
            "edges": edges,
            "root": root,
            "total_edges": len(candidates),
            "edges_truncated": len(candidates) > len(edges),
        }

    @app.get(prefix + "/snapshots/{snapshot_id}/tables/{table_name:path}", **operation("table_detail"))
    def table_detail(snapshot_id: str, table_name: str):
        findings = store.get(snapshot_id, "snapshot")["findings"]
        for table in findings["tables"]:
            if table["name"] == table_name:
                return table
        raise HTTPException(404, "TABLE_NOT_FOUND")

    @app.get(prefix + "/snapshots/{snapshot_id}/entity-graph", **operation("entity_graph"))
    def entity_graph(
        snapshot_id: str,
        root: str | None = None,
        origin: list[str] | None = Query(default=None),
        confidence: list[str] | None = Query(default=None),
        review: list[str] | None = Query(default=None),
        limit: int = Query(default=5000, ge=1, le=5000),
    ):
        origins = set(filter(None, origin)) if origin is not None else {"constraint", "dictionary", "inferred"}
        confidences = set(filter(None, confidence)) if confidence is not None else {"high", "medium", "low"}
        reviews = set(filter(None, review)) if review is not None else {"pending", "confirmed"}
        if (
            not origins <= {"constraint", "dictionary", "inferred"}
            or not confidences <= {"high", "medium", "low"}
            or not reviews <= {"pending", "confirmed", "rejected"}
        ):
            raise ValueError("INVALID_ENTITY_GRAPH_FILTER")
        findings = store.get(snapshot_id, "snapshot")["findings"]
        if root is not None and root not in {table["name"] for table in findings["tables"]}:
            raise ValueError("UNKNOWN_GRAPH_ROOT")
        candidates = [
            relation
            for relation in findings["relationships"]
            if relation["origin"] in origins
            and relation.get("confidence", "medium") in confidences
            and relation.get("review", "pending") in reviews
            and (root is None or root in (relation["source_table"], relation["target_table"]))
        ]
        candidates.sort(
            key=lambda relation: (
                relation.get("id", ""),
                relation["source_table"],
                relation["target_table"],
                tuple(relation["source_columns"]),
                tuple(relation["target_columns"]),
            )
        )
        names = {root} | {relation[key] for relation in candidates for key in ("source_table", "target_table")}
        nodes = [
            {
                "id": table["name"],
                "name": table["name"],
                "kind": table.get("kind", "table"),
                "comment": table.get("comment", ""),
                "column_count": len(table["columns"]),
            }
            for table in findings["tables"]
            if root is None or table["name"] in names
        ]
        return {
            "nodes": nodes,
            "relationships": candidates[:limit],
            "root": root,
            "total_relationships": len(candidates),
            "returned_relationships": min(limit, len(candidates)),
            "truncated": len(candidates) > limit,
        }

    @app.get(prefix + "/snapshots/{snapshot_id}/evidence/{evidence_id}", **operation("evidence"))
    def evidence(snapshot_id: str, evidence_id: str):
        return store.get(snapshot_id, "snapshot")["evidence"][evidence_id]

    @app.patch(prefix + "/relationships/{relationship_id}", **operation("review"))
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

    @app.post(prefix + "/snapshots/{snapshot_id}/questions", status_code=202, **operation("question", 202))
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

    @app.get(prefix + "/conversations", **operation("conversations"))
    def conversations(snapshot_id: str | None = None, offset: int = 0, limit: int = 20):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("INVALID_PAGINATION")
        if snapshot_id:
            store.get(snapshot_id, "snapshot")
        tasks = store.list("job")
        records = []
        for info in store.list("conversation"):
            if snapshot_id and info["snapshot_id"] != snapshot_id:
                continue
            history = sorted(
                [job for job in tasks if job.get("conversation_id") == info["id"]], key=lambda job: job["created"]
            )
            if not history:
                continue
            records.append(
                {
                    **info,
                    "title": history[0]["question"][:100],
                    "task_count": len(history),
                    "status": history[-1]["status"],
                    "updated": history[-1]["created"],
                }
            )
        records.sort(key=lambda info: info["updated"], reverse=True)
        return {"conversations": records[offset : offset + limit], "total": len(records)}

    @app.get(prefix + "/conversations/{conversation_id}", **operation("conversation"))
    def conversation(conversation_id: str):
        info = store.get(conversation_id, "conversation")
        info["answers"] = sorted(
            [answer for answer in store.list("answer") if answer["conversation_id"] == conversation_id],
            key=lambda a: a["created"],
        )
        info["tasks"] = [
            {key: value for key, value in job.items() if key not in ("draft", "evidence")}
            for job in sorted(store.list("job"), key=lambda job: job["created"])
            if job.get("conversation_id") == conversation_id
        ]
        return info

    @app.get(prefix + "/snapshots/{snapshot_id}/exports/{format}", **operation("export"))
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
