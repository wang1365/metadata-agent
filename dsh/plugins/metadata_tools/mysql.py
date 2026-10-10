from contextlib import contextmanager

from sqlalchemy import URL, create_engine, inspect, text


class MySQL:
    def __init__(self, config):
        self.database = config["database"]
        self.engine = create_engine(
            URL.create(
                "mysql+pymysql",
                username=config["username"],
                password=config["password"],
                host=config["host"],
                port=config["port"],
                database=self.database,
            ),
            connect_args={"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30},
            pool_pre_ping=True,
        )

    def close(self):
        self.engine.dispose()

    @contextmanager
    def connection(self):
        with self.engine.connect() as conn:
            conn.exec_driver_sql("SET SESSION MAX_EXECUTION_TIME=30000")
            conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
            conn.commit()
            try:
                yield conn
            finally:
                conn.rollback()

    def test(self):
        with self.connection() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar_one()
        return {
            "connected": True,
            "version": version,
            "warnings": [] if version.startswith(("8.0.", "8.4.")) else ["UNVERIFIED_MYSQL_VERSION"],
        }

    def objects(self):
        with self.connection() as conn:
            inspector = inspect(conn)
            return [{"name": name, "kind": "table"} for name in inspector.get_table_names()] + [
                {"name": name, "kind": "view"} for name in inspector.get_view_names()
            ]

    def describe(self, names):
        tables, foreign_keys, definitions, warnings = [], [], {}, []
        with self.connection() as conn:
            inspector = inspect(conn)
            for name in names:
                columns = [
                    {
                        "name": c["name"],
                        "type": str(c["type"]),
                        "nullable": c.get("nullable", True),
                        "default": str(c["default"]) if c.get("default") is not None else None,
                        "comment": c.get("comment") or "",
                    }
                    for c in inspector.get_columns(name)
                ]
                keys = []
                pk = inspector.get_pk_constraint(name)
                if pk.get("constrained_columns"):
                    keys.append(
                        {"kind": "primary", "columns": pk["constrained_columns"], "name": pk.get("name") or "PRIMARY"}
                    )
                for index in inspector.get_indexes(name):
                    if index.get("column_names") and all(index["column_names"]):
                        keys.append(
                            {
                                "kind": "unique" if index.get("unique") else "index",
                                "columns": index["column_names"],
                                "name": index["name"],
                            }
                        )
                kind = "table"
                try:
                    definition = inspector.get_view_definition(name)
                    if definition:
                        kind = "view"
                        definitions[name] = definition
                except Exception:
                    pass  # Physical tables do not have a view definition.
                fks = inspector.get_foreign_keys(name) if kind == "table" else []
                foreign_keys.extend({**fk, "table": name} for fk in fks)
                for fk in fks:
                    keys.append({"kind": "foreign", "columns": fk["constrained_columns"], "name": fk.get("name") or ""})
                tables.append(
                    {
                        "name": name,
                        "kind": kind,
                        "comment": inspector.get_table_comment(name).get("text") or "",
                        "columns": columns,
                        "keys": keys,
                    }
                )
        return tables, foreign_keys, definitions, warnings

    def definition(self, name, kind):
        with self.connection() as conn:
            if kind == "view":
                return inspect(conn).get_view_definition(name)
            if kind == "routine":
                return conn.execute(
                    text(
                        "SELECT ROUTINE_DEFINITION FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=:db AND ROUTINE_NAME=:name"
                    ),
                    {"db": self.database, "name": name},
                ).scalar()
            if kind == "trigger":
                return conn.execute(
                    text(
                        "SELECT ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=:db AND TRIGGER_NAME=:name"
                    ),
                    {"db": self.database, "name": name},
                ).scalar()
            raise ValueError("UNSUPPORTED_DEFINITION_KIND")

    def quote(self, name):
        return self.engine.dialect.identifier_preparer.quote_identifier(name)

    def sample(self, table, columns, limit):
        sql = f"SELECT /*+ MAX_EXECUTION_TIME(30000) */ {', '.join(self.quote(c) for c in columns)} FROM {self.quote(table)} LIMIT :limit"
        with self.connection() as conn:
            rows = conn.execute(text(sql), {"limit": limit}).mappings().all()
        return [{key: str(value) if value is not None else None for key, value in row.items()} for row in rows]

    def validate(self, source, source_columns, target, target_columns):
        # A bounded sample of the referencing side; indexed target lookup only.
        a = ", ".join(self.quote(c) for c in source_columns)
        b = " AND ".join(f"t.{self.quote(t)} = s.{self.quote(s)}" for s, t in zip(source_columns, target_columns))
        nonnull = " AND ".join(f"s.{self.quote(c)} IS NOT NULL" for c in source_columns)
        sql = f"SELECT /*+ MAX_EXECUTION_TIME(30000) */ COUNT(*) AS sampled, SUM({nonnull}) AS nonnull_count, SUM(({nonnull}) AND EXISTS(SELECT 1 FROM {self.quote(target)} t WHERE {b})) AS matched FROM (SELECT {a} FROM {self.quote(source)} LIMIT 1000) s"
        with self.connection() as conn:
            row = conn.execute(text(sql)).mappings().one()
        return {key: int(value or 0) for key, value in row.items()} | {
            "method": "first_1000_rows_not_random",
            "scope": "sample_only",
            "proves_constraint": False,
        }
