import json
import threading
from functools import wraps
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from metadata_service.models import Findings, FindingsInput, SchemaReference
from metadata_service.store import uid

from . import excel
from .mysql import MySQL


def serialized_save(function):
    @wraps(function)
    def save(self, *args, **kwargs):
        with self._save_lock:
            return function(self, *args, **kwargs)

    return save


class Capabilities:
    """DSH plugin I/O tools. All interpretation and result generation belong to DSH."""

    def __init__(self, store, job_id):
        self.store, self.job_id = store, job_id
        self._save_lock = threading.RLock()

    def job(self):
        return self.store.active(self.job_id)

    def source(self, expected=None):
        job = self.job()
        if job.get("question"):
            raise ValueError("SOURCE_TOOLS_DISABLED_FOR_QA")
        source = self.store.get(job["source_id"], "source")
        if expected and source["type"] != expected:
            raise ValueError("SOURCE_TYPE_MISMATCH")
        return source

    def mysql(self):
        source = self.source("mysql")
        config = dict(source["config"])
        config["password"] = self.store.settings.cipher.decrypt(source["password"].encode()).decode()
        return MySQL(config)

    def allowed_tables(self, database):
        selected = self.job().get("tables")
        if not selected:  # Compatibility for previously queued whole-database tasks.
            selected = [obj["name"] for obj in database.objects()]
        if len(selected) > 500:
            raise ValueError("TABLE_LIMIT_EXCEEDED")
        return selected

    def inspect_workbook(self) -> dict:
        """Inspect workbook sheets and merged cells before reading bounded regions."""
        source = self.source("excel")
        sheets = excel.inspect_workbook(source["path"])
        evidence_id = self.store.evidence(self.job_id, "excel_layout", {"file": source["name"]}, sheets)
        return {"sheets": sheets, "evidence_ids": [evidence_id]}

    def read_region(self, sheet: str, region: str) -> dict:
        """Read at most 2000 cells, e.g. Sheet1 / A1:H30. Coordinates are evidence."""
        source = self.source("excel")
        cells = excel.read_region(source["path"], sheet, region)
        evidence_id = self.store.evidence(
            self.job_id, "excel_region", {"file": source["name"], "sheet": sheet, "region": region}, cells
        )
        return {"cells": cells, "evidence_ids": [evidence_id]}

    def merge_input(self, findings, mode):
        chunk = FindingsInput.model_validate(findings).model_dump()
        if mode == "replace":
            return chunk
        if mode != "merge":
            raise ValueError("INVALID_SAVE_MODE")
        if len(chunk["tables"]) > 20:
            raise ValueError("BATCH_LIMIT: merge accepts at most 20 tables")
        draft = self.job()["draft"]
        for field, identity in [
            ("tables", lambda item: item["name"]),
            (
                "relationships",
                lambda item: (
                    item["source_table"],
                    tuple(item["source_columns"]),
                    item["target_table"],
                    tuple(item["target_columns"]),
                    item["origin"],
                ),
            ),
            (
                "lineage",
                lambda item: (
                    item["source_table"],
                    item.get("source_column"),
                    item["target_table"],
                    item.get("target_column"),
                    item.get("expression", ""),
                ),
            ),
        ]:
            merged = {identity(item): item for item in draft[field]}
            for item in chunk[field]:
                key = identity(item)
                if field == "relationships" and not item["id"] and key in merged:
                    item["id"] = merged[key]["id"]
                merged[key] = item
            draft[field] = list(merged.values())
        draft["warnings"] = list(dict.fromkeys(draft["warnings"] + chunk["warnings"]))
        return draft

    @serialized_save
    def save_dictionary_draft(self, findings: FindingsInput, mode: Literal["replace", "merge"] = "replace") -> dict:
        """Save a typed Excel draft. Use merge for batches of <=20 tables; wait for user confirmation after all batches."""
        self.source("excel")
        job = self.store.save_draft(self.job_id, self.merge_input(findings, mode), preview=True)
        return {
            "version": job["version"],
            "next": "Save remaining batches with mode=merge, then return for user confirmation; do not finalize.",
        }

    def list_objects(self) -> dict:
        """List selected MySQL objects. Never switches the task's data source."""
        database = self.mysql()
        try:
            selected = self.allowed_tables(database)
            return {"objects": [obj for obj in database.objects() if obj["name"] in selected]}
        finally:
            database.close()

    @serialized_save
    def describe_tables(self, names: list[str], refresh: bool = False) -> dict:
        """Batch-read 1..20 selected objects. Reuse this execution's evidence unless refresh=true; review it before save_findings(schema_refs=...)."""
        if not 1 <= len(names) <= 20 or len(set(names)) != len(names):
            raise ValueError("BATCH_LIMIT: provide 1..20 unique tables")
        job = self.job()
        self.source("mysql")
        if job.get("tables") and not set(names) <= set(job["tables"]):
            raise ValueError("TABLE_OUTSIDE_TASK")
        if not refresh:
            for ref, evidence in reversed(list(job["evidence"].items())):
                if (
                    evidence["kind"] == "mysql_schema"
                    and evidence["locator"].get("execution_id") == job.get("execution_id")
                    and set(evidence["locator"]["tables"]) == set(names)
                ):
                    data = evidence["data"]
                    return {
                        "tables": data["tables"],
                        "foreign_keys": data["foreign_keys"],
                        "warnings": data.get("warnings", []),
                        "views_with_definitions": list(data.get("definitions", {})),
                        "evidence_ids": [ref],
                        "cached": True,
                    }
                if (
                    evidence["kind"] == "mysql_schema"
                    and evidence["locator"].get("execution_id") == job.get("execution_id")
                    and set(names) & set(evidence["locator"]["tables"])
                ):
                    break  # A newer overlapping read invalidates this older batch.
        database = self.mysql()
        try:
            if not set(names) <= set(self.allowed_tables(database)):
                raise ValueError("TABLE_OUTSIDE_TASK")
            tables, foreign_keys, definitions, warnings = database.describe(names)
        except SQLAlchemyError:
            raise ValueError("MYSQL_OPERATION_FAILED: check connectivity and metadata privileges") from None
        finally:
            database.close()
        data = {"tables": tables, "foreign_keys": foreign_keys, "definitions": definitions, "warnings": warnings}
        evidence_id = self.store.evidence(
            self.job_id, "mysql_schema", {"tables": names, "execution_id": job.get("execution_id")}, data
        )
        return {
            "tables": tables,
            "foreign_keys": foreign_keys,
            "views_with_definitions": list(definitions),
            "evidence_ids": [evidence_id],
            "warnings": warnings,
            "cached": False,
        }

    @serialized_save
    def get_sql_definition(self, name: str, kind: str = "view") -> dict:
        """Read view, routine or trigger definition inside the task's selected database."""
        job = self.job()
        self.source("mysql")
        if job.get("tables") and (kind != "view" or name not in job["tables"]):
            raise ValueError("DEFINITION_OUTSIDE_TASK")
        for ref, evidence in reversed(list(job["evidence"].items())):
            if evidence["locator"].get("execution_id") != job.get("execution_id"):
                continue
            if (
                evidence["kind"] == "sql_definition"
                and evidence["locator"].get("name") == name
                and evidence["locator"].get("kind") == kind
            ):
                return {"sql": evidence["data"]["sql"], "evidence_ids": [ref], "cached": True}
            if kind == "view" and evidence["kind"] == "mysql_schema" and name in evidence["locator"]["tables"]:
                if name not in evidence["data"].get("definitions", {}):
                    return {"sql": None, "warnings": ["DEFINITION_UNAVAILABLE"], "cached": True}
                sql = evidence["data"]["definitions"][name]
                ref = self.store.evidence(
                    self.job_id,
                    "sql_definition",
                    {"name": name, "kind": kind, "execution_id": job.get("execution_id")},
                    {"sql": sql},
                )
                return {"sql": sql, "evidence_ids": [ref], "cached": True}
        database = self.mysql()
        try:
            if kind != "view" and self.job().get("tables"):
                raise ValueError("DEFINITION_OUTSIDE_TASK: selected-table tasks only read selected view definitions")
            if kind == "view" and name not in self.allowed_tables(database):
                raise ValueError("TABLE_OUTSIDE_TASK")
            sql = database.definition(name, kind)
        except SQLAlchemyError:
            raise ValueError("DEFINITION_UNAVAILABLE") from None
        finally:
            database.close()
        if not sql:
            return {"sql": None, "warnings": ["DEFINITION_UNAVAILABLE"]}
        evidence_id = self.store.evidence(
            self.job_id,
            "sql_definition",
            {"name": name, "kind": kind, "execution_id": job.get("execution_id")},
            {"sql": sql},
        )
        return {"sql": sql, "evidence_ids": [evidence_id]}

    def sample_rows(self, table: str, columns: list[str], limit: int = 20) -> dict:
        """Return bounded raw values to the model only if the user enabled samples. Not random."""
        if not self.job().get("allow_samples"):
            raise ValueError("SAMPLES_DISABLED")
        if not 1 <= limit <= 20 or not 1 <= len(columns) <= 20:
            raise ValueError("SAMPLE_LIMIT")
        self.check_columns(table, columns)
        self.store.budget(self.job_id, rows=limit)
        database = self.mysql()
        try:
            rows = database.sample(table, columns, limit)
        except SQLAlchemyError:
            raise ValueError("MYSQL_QUERY_FAILED") from None
        finally:
            database.close()
        return {
            "rows": [
                {key: str(value)[:200] if value is not None else None for key, value in row.items()} for row in rows
            ],
            "method": "first_rows_not_random",
            "budget": {"queries": self.job()["queries"], "sample_rows": self.job()["sample_rows"]},
        }

    def check_columns(self, table, columns):
        selected = self.job().get("tables")
        if selected and table not in selected:
            raise ValueError("TABLE_OUTSIDE_TASK")
        tables = {t["name"]: t for t in self.job()["draft"]["tables"]}
        if table not in tables or not columns or not set(columns) <= {c["name"] for c in tables[table]["columns"]}:
            raise ValueError("UNKNOWN_COLUMN")
        return tables[table]

    def profile_columns(self, table: str, columns: list[str]) -> dict:
        """Compute local first-1000-row null/uniqueness indicators. Raw values never leave this tool."""
        self.check_columns(table, columns)
        if len(columns) > 20:
            raise ValueError("COLUMN_LIMIT")
        self.store.budget(self.job_id)
        database = self.mysql()
        try:
            rows = database.sample(table, columns, 1000)
        except SQLAlchemyError:
            raise ValueError("MYSQL_QUERY_FAILED") from None
        finally:
            database.close()
        indicators = {
            col: {
                "sampled": len(rows),
                "nulls": sum(row[col] is None for row in rows),
                "distinct": len({row[col] for row in rows if row[col] is not None}),
            }
            for col in columns
        }
        evidence_id = self.store.evidence(
            self.job_id,
            "sample_indicators",
            {"table": table, "columns": columns, "method": "first_1000_not_random", "truncated_values": False},
            indicators,
        )
        return {"indicators": indicators, "scope": "sample_only", "evidence_ids": [evidence_id]}

    def validate_relationship(
        self, source_table: str, source_columns: list[str], target_table: str, target_columns: list[str]
    ) -> dict:
        """Validate sample references using indexed target lookup; never proves a declared constraint."""
        self.check_columns(source_table, source_columns)
        target = self.check_columns(target_table, target_columns)
        if len(source_columns) != len(target_columns):
            raise ValueError("ARITY_MISMATCH")
        if not any(
            key["columns"][: len(target_columns)] == target_columns
            for key in target["keys"]
            if key["kind"] in ("primary", "unique", "index")
        ):
            raise ValueError("VALIDATION_SKIPPED: target lookup has no matching leading index")
        self.store.budget(self.job_id)
        database = self.mysql()
        try:
            indicators = database.validate(source_table, source_columns, target_table, target_columns)
        except SQLAlchemyError:
            raise ValueError("MYSQL_QUERY_FAILED") from None
        finally:
            database.close()
        evidence_id = self.store.evidence(
            self.job_id,
            "relationship_validation",
            {
                "source_table": source_table,
                "source_columns": source_columns,
                "target_table": target_table,
                "target_columns": target_columns,
            },
            indicators,
        )
        return {"validation": indicators, "evidence_ids": [evidence_id]}

    def catalog(self):
        job = self.job()
        if job.get("question"):
            snap = self.store.get(job["bound_snapshot_id"], "snapshot")
            return snap["findings"], snap["evidence"]
        return job["draft"], job["evidence"]

    def search_metadata(self, query: str = "", offset: int = 0, limit: int = 20) -> dict:
        """Search task/snapshot tables, columns and comments with bounded pagination."""
        if offset < 0 or not 1 <= limit <= 20:
            raise ValueError("PAGINATION_LIMIT")
        findings, _ = self.catalog()
        tables = [
            table for table in findings["tables"] if query.lower() in json.dumps(table, ensure_ascii=False).lower()
        ]
        return {
            "tables": tables[offset : offset + limit],
            "total": len(tables),
            "relationships": findings["relationships"][:50],
            "warnings": findings["warnings"],
        }

    def get_neighbors(self, table: str, kind: str = "er") -> dict:
        """Read a one-hop relationship or lineage subgraph, including evidence references."""
        findings, _ = self.catalog()
        if kind not in ("er", "lineage"):
            raise ValueError("INVALID_GRAPH_KIND")
        edges = [
            edge
            for edge in findings["relationships" if kind == "er" else "lineage"]
            if table in (edge["source_table"], edge["target_table"])
        ]
        return {"edges": edges[:200], "truncated": len(edges) > 200}

    def read_evidence(self, evidence_ids: list[str]) -> dict:
        """Read up to 20 evidence records from this task or bound snapshot."""
        if len(evidence_ids) > 20:
            raise ValueError("EVIDENCE_LIMIT")
        _, evidence = self.catalog()
        if not set(evidence_ids) <= set(evidence):
            raise ValueError("UNKNOWN_EVIDENCE")
        return {"evidence": [evidence[ref] for ref in evidence_ids]}

    @serialized_save
    def save_findings(
        self,
        findings: FindingsInput,
        mode: Literal["replace", "merge"] = "replace",
        schema_refs: list[SchemaReference] | None = None,
    ) -> dict:
        """Save DSH findings. With mode=merge, explicitly adopt <=20 reviewed MySQL tables via schema_refs (evidence_id + names) instead of reprinting columns. References copy raw structure only, never generate relationships or lineage."""
        job = self.job()
        self.source()
        if schema_refs:
            if mode != "merge":
                raise ValueError("SCHEMA_REFS_REQUIRE_MERGE")
            if len(schema_refs) > 20:
                raise ValueError("BATCH_LIMIT")
            self.source("mysql")
            chunk = FindingsInput.model_validate(findings).model_dump()
            adopted = {table["name"] for table in chunk["tables"]}
            for value in schema_refs:
                reference = SchemaReference.model_validate(value)
                evidence = job["evidence"].get(reference.evidence_id, {})
                if evidence.get("kind") != "mysql_schema":
                    raise ValueError("SCHEMA_EVIDENCE_REQUIRED")
                raw = {table["name"]: table for table in evidence["data"]["tables"]}
                for name in reference.names:
                    if name not in raw:
                        raise ValueError("TABLE_NOT_IN_EVIDENCE")
                    if name in adopted:
                        raise ValueError("DUPLICATE_SCHEMA_REFERENCE")
                    table = {
                        key: raw[name][key]
                        for key in ("name", "kind", "comment", "columns", "keys")
                        if key in raw[name]
                    }
                    table["evidence_ids"] = [reference.evidence_id]
                    table["columns"] = [
                        {**column, "evidence_ids": [reference.evidence_id]} for column in table["columns"]
                    ]
                    chunk["tables"].append(table)
                    adopted.add(name)
            findings = chunk
        parsed = Findings.model_validate(self.merge_input(findings, mode))
        if self.source()["type"] == "mysql" and job.get("tables"):
            if not {table.name for table in parsed.tables} <= set(job["tables"]):
                raise ValueError("TABLE_OUTSIDE_TASK")
        original = Findings.model_validate(job["draft"])
        if job["confirmed"] and parsed.tables != original.tables:
            raise ValueError("CONFIRMED_MAPPING_IMMUTABLE")
        # Validate citations against raw constraints; never infer a relationship here.
        constraints = {r.id: r for r in original.relationships if r.origin == "constraint"}
        for relation in parsed.relationships:
            if relation.origin != "constraint":
                continue
            if relation.id in constraints and relation != constraints[relation.id]:
                raise ValueError("DECLARED_CONSTRAINT_IMMUTABLE")
            supported = any(
                fk["table"] == relation.source_table
                and fk["constrained_columns"] == relation.source_columns
                and fk["referred_table"] == relation.target_table
                and fk["referred_columns"] == relation.target_columns
                and (not fk.get("referred_schema") or fk["referred_schema"] == self.source()["config"].get("database"))
                for ref in relation.evidence_ids
                for ev in [job["evidence"].get(ref, {})]
                if ev.get("kind") == "mysql_schema"
                for fk in ev["data"]["foreign_keys"]
            )
            if not supported:
                raise ValueError("DECLARED_CONSTRAINT_WITHOUT_EVIDENCE")
        if not set(constraints) <= {r.id for r in parsed.relationships}:
            raise ValueError("DECLARED_CONSTRAINT_CANNOT_BE_DROPPED")
        saved = self.store.save_draft(self.job_id, parsed.model_dump())
        return {"version": saved["version"], "table_count": len(saved["draft"]["tables"])}

    def finalize_snapshot(self) -> dict:
        """Validate and publish a nonempty snapshot. Natural-language completion is insufficient."""
        self.source()
        return {"snapshot_id": self.store.publish(self.job_id)}

    def save_answer(self, answer: str, evidence_ids: list[str]) -> dict:
        """Persist an answer with citations from the bound snapshot. Explain missing evidence explicitly."""
        job = self.job()
        if not job.get("question") or not answer.strip() or len(answer) > 20000:
            raise ValueError("INVALID_ANSWER")
        _, evidence = self.catalog()
        if not set(evidence_ids) <= set(evidence):
            raise ValueError("UNKNOWN_EVIDENCE")
        answer_id = job["answer_id"] or uid()
        if not job["answer_id"]:
            self.store.create(
                "answer",
                {
                    "snapshot_id": job["bound_snapshot_id"],
                    "conversation_id": job["conversation_id"],
                    "question": job["question"],
                    "answer": answer,
                    "evidence_ids": evidence_ids,
                },
                answer_id,
            )
            self.store.update(self.job_id, answer_id=answer_id)
        return {"answer_id": answer_id}
