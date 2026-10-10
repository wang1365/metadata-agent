import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event, text

from dsh.plugins.metadata_tools.capabilities import Capabilities
from dsh.plugins.metadata_tools.mysql import MySQL


@pytest.fixture
def batch_database():
    engine = create_engine("sqlite://")
    connection = engine.connect()
    connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS information_schema")
    definitions = {
        "TABLES": "TABLE_SCHEMA TEXT, TABLE_NAME TEXT, TABLE_TYPE TEXT, TABLE_COMMENT TEXT",
        "COLUMNS": "TABLE_SCHEMA TEXT, TABLE_NAME TEXT, COLUMN_NAME TEXT, COLUMN_TYPE TEXT, IS_NULLABLE TEXT, COLUMN_DEFAULT TEXT, COLUMN_COMMENT TEXT, ORDINAL_POSITION INTEGER",
        "STATISTICS": "TABLE_SCHEMA TEXT, TABLE_NAME TEXT, INDEX_NAME TEXT, NON_UNIQUE INTEGER, COLUMN_NAME TEXT, SEQ_IN_INDEX INTEGER",
        "KEY_COLUMN_USAGE": "TABLE_SCHEMA TEXT, TABLE_NAME TEXT, CONSTRAINT_SCHEMA TEXT, CONSTRAINT_NAME TEXT, COLUMN_NAME TEXT, REFERENCED_TABLE_SCHEMA TEXT, REFERENCED_TABLE_NAME TEXT, REFERENCED_COLUMN_NAME TEXT, ORDINAL_POSITION INTEGER",
        "REFERENTIAL_CONSTRAINTS": "CONSTRAINT_SCHEMA TEXT, TABLE_NAME TEXT, CONSTRAINT_NAME TEXT, UPDATE_RULE TEXT, DELETE_RULE TEXT",
        "VIEWS": "TABLE_SCHEMA TEXT, TABLE_NAME TEXT, VIEW_DEFINITION TEXT",
    }
    for table, columns in definitions.items():
        connection.exec_driver_sql(f"CREATE TABLE information_schema.{table} ({columns})")
    for i in range(20):
        name = f"table_{i}"
        connection.execute(
            text("INSERT INTO information_schema.TABLES VALUES ('fixture', :name, 'BASE TABLE', 'raw comment')"),
            {"name": name},
        )
        for ordinal, column in [(2, "b"), (1, "a")]:
            connection.execute(
                text(
                    "INSERT INTO information_schema.COLUMNS VALUES ('fixture', :name, :column, 'bigint unsigned', 'NO', '0', 'raw field', :ordinal)"
                ),
                {"name": name, "column": column, "ordinal": ordinal},
            )
            connection.execute(
                text(
                    "INSERT INTO information_schema.STATISTICS VALUES ('fixture', :name, 'PRIMARY', 0, :column, :ordinal)"
                ),
                {"name": name, "column": column, "ordinal": ordinal},
            )
    connection.exec_driver_sql(
        "INSERT INTO information_schema.REFERENTIAL_CONSTRAINTS VALUES ('fixture', 'table_1', 'fk_pair', 'CASCADE', 'RESTRICT')"
    )
    for ordinal, column in [(2, "b"), (1, "a")]:
        connection.execute(
            text(
                "INSERT INTO information_schema.KEY_COLUMN_USAGE VALUES ('fixture','table_1','fixture','fk_pair',:column,'fixture','table_0',:column,:ordinal)"
            ),
            {"column": column, "ordinal": ordinal},
        )
    queries = []

    @event.listens_for(engine, "before_cursor_execute")
    def observe(_conn, _cursor, statement, parameters, _context, _executemany):
        if statement.startswith("SELECT"):
            queries.append((statement, parameters))

    database = MySQL.__new__(MySQL)
    database.database = "fixture"

    @contextmanager
    def connect():
        yield connection

    database.connection = connect
    yield database, queries, connection
    connection.close()
    engine.dispose()


def test_twenty_tables_require_four_selects_and_preserve_ordered_raw_keys(batch_database):
    database, queries, _ = batch_database
    tables, foreign_keys, definitions, warnings = database.describe([f"table_{i}" for i in range(20)])
    assert len(queries) == 4 and len(tables) == 20
    assert [column["name"] for column in tables[0]["columns"]] == ["a", "b"]
    assert tables[0]["keys"] == [{"name": "PRIMARY", "kind": "primary", "columns": ["a", "b"]}]
    assert foreign_keys[0]["constrained_columns"] == ["a", "b"]
    assert foreign_keys[0]["referred_columns"] == ["a", "b"]
    assert foreign_keys[0]["options"] == {"onupdate": "CASCADE", "ondelete": "RESTRICT"}
    assert definitions == {} and warnings == []
    queries.clear()
    database.describe(["table_0"])
    assert len(queries) == 4  # Query count does not grow per table.


def test_views_missing_definitions_functional_indexes_and_parameter_binding(batch_database):
    database, queries, connection = batch_database
    connection.exec_driver_sql("UPDATE information_schema.TABLES SET TABLE_TYPE='VIEW' WHERE TABLE_NAME='table_1'")
    connection.exec_driver_sql("INSERT INTO information_schema.VIEWS VALUES ('fixture', 'table_1', NULL)")
    connection.exec_driver_sql(
        "INSERT INTO information_schema.STATISTICS VALUES ('fixture','table_0','functional',0,NULL,1)"
    )
    _, _, _, warnings = database.describe(["table_0", "table_1"])
    assert len(queries) == 5
    assert "DEFINITION_UNAVAILABLE:table_1" in warnings
    assert "FUNCTIONAL_INDEX_UNSUPPORTED:table_0:functional" in warnings
    queries.clear()
    malicious = "table_0') OR 1=1 --"
    with pytest.raises(ValueError, match="UNKNOWN_TABLE"):
        database.describe([malicious])
    assert malicious not in queries[0][0] and malicious in queries[0][1]


def test_cached_evidence_and_explicit_refs_do_not_generate_analysis(store):
    source = store.create("source", {"type": "mysql", "config": {"database": "fixture"}})
    job = store.new_job(source, tables=["a", "b"])
    store.claim(job)
    tools = Capabilities(store, job)

    class Database:
        calls = 0

        def describe(self, names):
            self.calls += 1
            return (
                [
                    {"name": name, "kind": "view", "columns": [{"name": "id", "type": "bigint"}], "keys": []}
                    for name in names
                ],
                [],
                {"a": "SELECT id FROM b"},
                [],
            )

        def close(self):
            pass

    database = Database()
    tools.mysql = lambda: database
    result = tools.describe_tables(["a", "b"])
    ref = result["evidence_ids"][0]
    assert store.get(job)["draft"]["tables"] == []  # Reading is never acceptance.
    cached = tools.describe_tables(["b", "a"])
    assert cached["cached"] and cached["evidence_ids"] == [ref] and database.calls == 1
    assert "definitions" not in cached
    assert tools.get_sql_definition("a")["sql"] == "SELECT id FROM b"
    assert tools.get_sql_definition("a")["cached"]
    refs = [{"evidence_id": ref, "names": ["a", "b"]}]
    tools.save_findings({}, mode="merge", schema_refs=refs)
    draft = store.get(job)["draft"]
    assert {table["name"] for table in draft["tables"]} == {"a", "b"}
    assert all(table["columns"][0]["evidence_ids"] == [ref] for table in draft["tables"])
    assert draft["relationships"] == [] and draft["lineage"] == []
    assert len(json.dumps({"schema_refs": refs})) < len(json.dumps({"tables": draft["tables"]}))
    tools.describe_tables(["a", "b"], refresh=True)
    assert database.calls == 2
    database.describe = lambda names: ([{"name": name, "columns": [{"name": "new_id"}]} for name in names], [], {}, [])
    tools.describe_tables(["a"], refresh=True)
    assert tools.get_sql_definition("a")["sql"] is None  # Do not revive a definition from older evidence.
    assert not tools.describe_tables(["a", "b"])["cached"]  # A newer overlapping read invalidates the old batch.
    assert tools.describe_tables(["a", "b"])["cached"]
    database.describe = Database.describe.__get__(database)
    store.update(job, execution_id="next-execution")
    tools.describe_tables(["a", "b"])
    assert database.calls == 3
    for refs, code in [
        ([{"evidence_id": "unknown", "names": ["a"]}], "SCHEMA_EVIDENCE_REQUIRED"),
        ([{"evidence_id": ref, "names": ["missing"]}], "TABLE_NOT_IN_EVIDENCE"),
        ([{"evidence_id": ref, "names": ["a", "a"]}], "DUPLICATE_SCHEMA_REFERENCE"),
    ]:
        with pytest.raises(ValueError, match=code):
            tools.save_findings({}, mode="merge", schema_refs=refs)
