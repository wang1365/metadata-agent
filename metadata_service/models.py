from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Column(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    type: str = "unknown"
    comment: str = ""
    nullable: bool = True
    default: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Key(StrictModel):
    kind: Literal["primary", "unique", "index", "foreign"]
    columns: list[str] = Field(min_length=1)
    name: str = ""


class Table(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    comment: str = ""
    kind: Literal["table", "view"] = "table"
    columns: list[Column] = Field(min_length=1)
    keys: list[Key] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_columns(self):
        names = [column.name for column in self.columns]
        if len(set(names)) != len(names):
            raise ValueError("DUPLICATE_COLUMN")
        if any(column not in names for key in self.keys for column in key.columns):
            raise ValueError("UNKNOWN_KEY_COLUMN")
        return self


class Relationship(StrictModel):
    id: str = ""
    source_table: str
    source_columns: list[str] = Field(min_length=1)
    target_table: str
    target_columns: list[str] = Field(min_length=1)
    origin: Literal["dictionary", "constraint", "inferred"]
    confidence: Literal["high", "medium", "low"] = "medium"
    explanation: str
    evidence_ids: list[str] = Field(min_length=1)
    validation: dict = Field(default_factory=dict)
    review: Literal["pending", "confirmed", "rejected"] = "pending"

    @model_validator(mode="after")
    def check_arity(self):
        if len(self.source_columns) != len(self.target_columns):
            raise ValueError("RELATIONSHIP_ARITY_MISMATCH")
        return self


class LineageEdge(StrictModel):
    source_table: str
    target_table: str
    source_column: str | None = None
    target_column: str | None = None
    expression: str = ""
    evidence_ids: list[str] = Field(min_length=1)


class FindingsInput(StrictModel):
    """Tool input schema; cross-object references are checked after merging a batch."""

    tables: list[Table] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    lineage: list[LineageEdge] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SchemaReference(StrictModel):
    """Explicit DSH selection of already-read raw table records, not inferred results."""

    evidence_id: str
    names: list[str] = Field(min_length=1, max_length=20)


class Findings(FindingsInput):
    @model_validator(mode="after")
    def check_objects(self):
        by_name = {table.name: table for table in self.tables}
        if len(by_name) != len(self.tables):
            raise ValueError("DUPLICATE_TABLE")
        if len(self.tables) > 500 or sum(len(t.columns) for t in self.tables) > 20000:
            raise ValueError("OBJECT_LIMIT_EXCEEDED")
        for relation in self.relationships:
            for table_name, cols in [
                (relation.source_table, relation.source_columns),
                (relation.target_table, relation.target_columns),
            ]:
                if table_name not in by_name or not set(cols) <= {c.name for c in by_name[table_name].columns}:
                    raise ValueError("UNKNOWN_RELATIONSHIP_OBJECT")
        for edge in self.lineage:
            for name, column in [(edge.source_table, edge.source_column), (edge.target_table, edge.target_column)]:
                if name not in by_name or (column and column not in {c.name for c in by_name[name].columns}):
                    raise ValueError("UNKNOWN_LINEAGE_OBJECT")
        return self


class MySQLSource(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1)
    port: int = Field(default=3306, ge=1, le=65535)
    username: str = Field(min_length=1)
    password: str
    database: str = Field(min_length=1)


class AnalysisRequest(StrictModel):
    source_id: str
    tables: list[str] = Field(default_factory=list, max_length=500)
    allow_samples: bool = False


class MappingRequest(StrictModel):
    version: int
    findings: Findings


class QuestionRequest(StrictModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class ReviewRequest(StrictModel):
    status: Literal["pending", "confirmed", "rejected"]
    note: str = Field(default="", max_length=2000)


class SamplePreviewRequest(StrictModel):
    table: str = Field(min_length=1, max_length=256)
    columns: list[str] = Field(min_length=1, max_length=20)
    limit: int = Field(default=10, ge=1, le=20)
