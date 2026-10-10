import copy
import json
import os
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, delete, event, select, update

from .models import Findings


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


TERMINAL = {"succeeded", "partial_success", "failed", "cancelled"}


def job_queue(job):
    return "qa" if job.get("question") else "analysis"


class Store:
    def __init__(self, settings):
        self.settings = settings
        self.engine = create_engine(
            f"sqlite:///{settings.data_dir / 'metadata.sqlite3'}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )

        @event.listens_for(self.engine, "connect")
        def configure(dbapi, _record):
            dbapi.execute("PRAGMA journal_mode=WAL")
            dbapi.execute("PRAGMA foreign_keys=ON")

        metadata = MetaData()
        self.records = Table(
            "records",
            metadata,
            Column("id", String, primary_key=True),
            Column("kind", String, index=True),
            Column("created", String),
            Column("payload", JSON),
        )
        self.events = Table(
            "task_events",
            metadata,
            Column("seq", Integer, primary_key=True, autoincrement=True),
            Column("job_id", String, index=True, nullable=False),
            Column("time", String, nullable=False),
            Column("payload", JSON, nullable=False),
        )
        metadata.create_all(self.engine)

    def log(self, job_id, event_type, message, *, level="info", tool=None, duration_ms=None, code=None, stage=None):
        """Only callers' fixed summaries belong here, never raw DSH/tool payloads."""
        job = self.get(job_id, "job")
        payload = {
            "execution_id": job.get("execution_id"),
            "origin_pid": os.getpid(),
            "type": event_type,
            "level": level,
            "message": message,
        }
        payload.update(
            {k: v for k, v in {"tool": tool, "duration_ms": duration_ms, "code": code}.items() if v is not None}
        )
        timestamp = now()
        with self.engine.begin() as connection:
            seq = connection.execute(
                self.events.insert().values(job_id=job_id, time=timestamp, payload=payload)
            ).inserted_primary_key[0]
            cutoff = connection.execute(
                select(self.events.c.seq)
                .where(self.events.c.job_id == job_id)
                .order_by(self.events.c.seq.desc())
                .offset(4999)
                .limit(1)
            ).scalar()
            if cutoff:
                connection.execute(
                    delete(self.events).where(self.events.c.job_id == job_id, self.events.c.seq < cutoff)
                )
        if stage:

            def progress(payload):
                if payload["status"] == "running" and not payload["cancel_requested"]:
                    payload["stage"] = stage
                    payload["last_activity_at"] = timestamp

            self.mutate(job_id, progress)
        record = {"seq": seq, "job_id": job_id, "time": timestamp, **payload}
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)
        return record

    def logs(self, job_id, after=0, limit=200):
        job = self.get(job_id, "job")
        with self.engine.connect() as connection:
            first = connection.execute(
                select(self.events.c.seq).where(self.events.c.job_id == job_id).order_by(self.events.c.seq).limit(1)
            ).scalar()
            rows = (
                connection.execute(
                    select(self.events)
                    .where(self.events.c.job_id == job_id, self.events.c.seq > after)
                    .order_by(self.events.c.seq)
                    .limit(limit + 1)
                )
                .mappings()
                .all()
            )
        items = [{"seq": row["seq"], "job_id": job_id, "time": row["time"], **row["payload"]} for row in rows[:limit]]
        return {
            "events": items,
            "next_cursor": items[-1]["seq"] if items else after,
            "has_more": len(rows) > limit,
            "truncated": bool(after and first and after < first - 1),
            "status": job["status"],
        }

    def create(self, kind, data, record_id=None):
        record_id = record_id or uid()
        with self.engine.begin() as connection:
            connection.execute(self.records.insert().values(id=record_id, kind=kind, created=now(), payload=data))
        return record_id

    def get(self, record_id, kind=None):
        with self.engine.connect() as connection:
            row = connection.execute(select(self.records).where(self.records.c.id == record_id)).mappings().first()
        if not row or (kind and row["kind"] != kind):
            raise KeyError("NOT_FOUND")
        return {"id": row["id"], "created": row["created"], **row["payload"]}

    def list(self, kind):
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(self.records).where(self.records.c.kind == kind).order_by(self.records.c.created.desc())
                )
                .mappings()
                .all()
            )
        return [{"id": row["id"], "created": row["created"], **row["payload"]} for row in rows]

    def mutate(self, record_id, operation):
        # Immediate transaction serializes budget reservations, cancellation and tool writes.
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                row = connection.execute(select(self.records).where(self.records.c.id == record_id)).mappings().first()
                if not row:
                    raise KeyError("NOT_FOUND")
                payload = copy.deepcopy(row["payload"])
                operation(payload)
                connection.execute(update(self.records).where(self.records.c.id == record_id).values(payload=payload))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get(record_id)

    def update(self, record_id, **changes):
        return self.mutate(record_id, lambda payload: payload.update(changes))

    def new_job(self, source_id=None, **extra):
        if source_id:
            self.get(source_id, "source")
        return self.create(
            "job",
            {
                "source_id": source_id,
                "status": "queued",
                "stage": "等待运行",
                "cancel_requested": False,
                "confirmed": False,
                "draft": Findings().model_dump(),
                "version": 0,
                "queries": 0,
                "sample_rows": 0,
                "evidence": {},
                "snapshot_id": None,
                "answer_id": None,
                "error": None,
                **extra,
                "queue": "qa" if extra.get("question") else "analysis",
            },
        )

    def claim(self, job_id):
        def claim(payload):
            if payload["status"] != "queued":
                raise ValueError("NOT_QUEUED")
            payload.update(status="running", stage="启动 DSH", execution_id=uid(), started=now())

        return self.mutate(job_id, claim)

    def claim_next(self, queue):
        if queue not in ("analysis", "qa"):
            raise ValueError("INVALID_QUEUE")
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                rows = (
                    connection.execute(
                        select(self.records)
                        .where(self.records.c.kind == "job")
                        .order_by(self.records.c.created, self.records.c.id)
                    )
                    .mappings()
                    .all()
                )
                active_conversations = {
                    row["payload"].get("conversation_id")
                    for row in rows
                    if row["payload"]["status"] == "running" and job_queue(row["payload"]) == "qa"
                }
                chosen = next(
                    (
                        row
                        for row in rows
                        if row["payload"]["status"] == "queued"
                        and job_queue(row["payload"]) == queue
                        and (queue != "qa" or row["payload"].get("conversation_id") not in active_conversations)
                    ),
                    None,
                )
                if chosen is None:
                    connection.commit()
                    return None
                payload = dict(chosen["payload"])
                payload.update(status="running", stage="启动 DSH", execution_id=uid(), started=now(), queue=queue)
                connection.execute(
                    update(self.records).where(self.records.c.id == chosen["id"]).values(payload=payload)
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get(chosen["id"], "job")

    def active(self, job_id):
        job = self.get(job_id, "job")
        if job["status"] != "running" or job["cancel_requested"]:
            raise ValueError("TASK_NOT_ACTIVE")
        return job

    def budget(self, job_id, rows=0):
        def reserve(payload):
            if payload["status"] != "running" or payload["cancel_requested"]:
                raise ValueError("TASK_NOT_ACTIVE")
            if payload["queries"] >= 30 or payload["sample_rows"] + rows > 200:
                raise ValueError("BUDGET_EXHAUSTED")
            payload["queries"] += 1
            payload["sample_rows"] += rows

        self.mutate(job_id, reserve)

    def evidence(self, job_id, kind, locator, data):
        evidence_id = uid()

        def save(payload):
            if payload["status"] != "running" or payload["cancel_requested"]:
                raise ValueError("TASK_NOT_ACTIVE")
            payload["evidence"][evidence_id] = {"id": evidence_id, "kind": kind, "locator": locator, "data": data}

        self.mutate(job_id, save)
        return evidence_id

    @staticmethod
    def check_evidence(findings, evidence):
        items = findings.tables + findings.relationships + findings.lineage
        refs = [ref for item in items for ref in item.evidence_ids]
        refs += [ref for table in findings.tables for col in table.columns for ref in col.evidence_ids]
        if not set(refs) <= set(evidence):
            raise ValueError("UNKNOWN_EVIDENCE")
        if any(not table.evidence_ids for table in findings.tables):
            raise ValueError("TABLE_EVIDENCE_REQUIRED")

    def save_draft(self, job_id, findings, preview=False):
        parsed = Findings.model_validate(findings)

        def save(payload):
            if payload["status"] != "running" or payload["cancel_requested"]:
                raise ValueError("TASK_NOT_ACTIVE")
            if preview and payload["confirmed"]:
                raise ValueError("MAPPING_ALREADY_CONFIRMED")
            self.check_evidence(parsed, payload["evidence"])
            for relation in parsed.relationships:
                relation.id = relation.id or uid()
            payload.update(
                draft=parsed.model_dump(),
                version=payload["version"] + 1,
                stage="解析草稿已保存" if preview else "发现已保存",
            )
            if preview:
                payload["preview_ready"] = True

        return self.mutate(job_id, save)

    def publish(self, job_id):
        job = self.active(job_id)
        findings = Findings.model_validate(job["draft"])
        if not findings.tables:
            raise ValueError("EMPTY_SNAPSHOT")
        if self.get(job["source_id"], "source")["type"] == "excel" and not job["confirmed"]:
            raise ValueError("MAPPING_NOT_CONFIRMED")
        self.check_evidence(findings, job["evidence"])
        snapshot_id = job["snapshot_id"] or uid()

        # Publish and attach atomically, so cancellation cannot expose an orphan success.
        def publish(payload):
            if payload["status"] != "running" or payload["cancel_requested"]:
                raise ValueError("TASK_NOT_ACTIVE")
            payload["snapshot_id"] = snapshot_id

        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                row = connection.execute(select(self.records).where(self.records.c.id == job_id)).mappings().one()
                payload = row["payload"]
                publish(payload)
                existing = connection.execute(select(self.records.c.id).where(self.records.c.id == snapshot_id)).first()
                if not existing:
                    connection.execute(
                        self.records.insert().values(
                            id=snapshot_id,
                            kind="snapshot",
                            created=now(),
                            payload={
                                "source_id": job["source_id"],
                                "job_id": job_id,
                                "findings": findings.model_dump(),
                                "evidence": job["evidence"],
                                "reviews": [],
                            },
                        )
                    )
                connection.execute(update(self.records).where(self.records.c.id == job_id).values(payload=payload))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return snapshot_id
