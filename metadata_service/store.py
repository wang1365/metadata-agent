import copy
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, event, select, update

from .models import Findings


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


TERMINAL = {"succeeded", "partial_success", "failed", "cancelled"}


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
        metadata.create_all(self.engine)

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
            },
        )

    def claim(self, job_id):
        def claim(payload):
            if payload["status"] != "queued":
                raise ValueError("NOT_QUEUED")
            payload.update(status="running", stage="启动 DSH", execution_id=uid(), started=now())

        return self.mutate(job_id, claim)

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
