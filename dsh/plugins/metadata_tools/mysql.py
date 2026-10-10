import time
from contextlib import contextmanager

import regex
from sqlalchemy import URL, bindparam, create_engine, inspect, text


def filter_objects(items, query="", match="fuzzy"):
    """Filter raw object names only; never interprets metadata or expands scope."""
    if match not in ("fuzzy", "regex") or len(query) > 256:
        raise ValueError("INVALID_OBJECT_FILTER")
    if match == "fuzzy":
        return [item for item in items if query.casefold() in item["name"].casefold()]
    try:
        pattern = regex.compile(query, regex.IGNORECASE)
    except regex.error:
        raise ValueError("INVALID_REGEX") from None
    deadline = time.monotonic() + 0.2
    selected = []
    for item in items:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("REGEX_TIMEOUT")
        try:
            if pattern.search(item["name"], timeout=remaining):
                selected.append(item)
        except TimeoutError:
            raise ValueError("REGEX_TIMEOUT") from None
    return selected


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
        """Read a selected batch with four catalog SELECTs plus one for views.

        Grouping reflects declared database structures only; no relationship or
        lineage conclusions are generated here.
        """
        if not 1 <= len(names) <= 20 or len(names) != len(set(names)):
            raise ValueError("BATCH_LIMIT")
        with self.connection() as conn:

            def read(sql):
                statement = text(sql).bindparams(bindparam("names", expanding=True))
                return conn.execute(statement, {"db": self.database, "names": names}).mappings().all()

            objects = read(
                "SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=:db AND TABLE_NAME IN :names ORDER BY TABLE_NAME"
            )
            if {row["TABLE_NAME"] for row in objects} != set(names):
                raise ValueError("UNKNOWN_TABLE: selected objects unavailable")
            columns = read(
                "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_COMMENT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=:db AND TABLE_NAME IN :names ORDER BY TABLE_NAME, ORDINAL_POSITION"
            )
            indexes = read(
                "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, COLUMN_NAME, SEQ_IN_INDEX FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=:db AND TABLE_NAME IN :names ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
            )
            constraints = read(
                "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, r.UPDATE_RULE, r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k LEFT JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.TABLE_NAME=k.TABLE_NAME AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME WHERE k.TABLE_SCHEMA=:db AND k.TABLE_NAME IN :names AND k.REFERENCED_TABLE_NAME IS NOT NULL ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION"
            )
            views = (
                read(
                    "SELECT TABLE_NAME, VIEW_DEFINITION FROM information_schema.VIEWS WHERE TABLE_SCHEMA=:db AND TABLE_NAME IN :names ORDER BY TABLE_NAME"
                )
                if any(row["TABLE_TYPE"] == "VIEW" for row in objects)
                else []
            )

        tables = {
            row["TABLE_NAME"]: {
                "name": row["TABLE_NAME"],
                "kind": "view" if row["TABLE_TYPE"] == "VIEW" else "table",
                "comment": row["TABLE_COMMENT"] or "",
                "columns": [],
                "keys": [],
            }
            for row in objects
        }
        for row in columns:
            tables[row["TABLE_NAME"]]["columns"].append(
                {
                    "name": row["COLUMN_NAME"],
                    "type": row["COLUMN_TYPE"],
                    "nullable": row["IS_NULLABLE"] == "YES",
                    "default": str(row["COLUMN_DEFAULT"]) if row["COLUMN_DEFAULT"] is not None else None,
                    "comment": row["COLUMN_COMMENT"] or "",
                }
            )
        if any(not table["columns"] for table in tables.values()):
            raise ValueError("SCHEMA_UNAVAILABLE: selected columns unavailable")
        grouped_indexes = {}
        for row in indexes:
            grouped_indexes.setdefault((row["TABLE_NAME"], row["INDEX_NAME"]), []).append(row)
        warnings = []
        for (name, index), rows in grouped_indexes.items():
            if any(row["COLUMN_NAME"] is None for row in rows):
                warnings.append(f"FUNCTIONAL_INDEX_UNSUPPORTED:{name}:{index}")
                continue
            tables[name]["keys"].append(
                {
                    "name": index,
                    "kind": "primary" if index == "PRIMARY" else "unique" if not rows[0]["NON_UNIQUE"] else "index",
                    "columns": [row["COLUMN_NAME"] for row in rows],
                }
            )
        grouped_fks = {}
        for row in constraints:
            grouped_fks.setdefault((row["TABLE_NAME"], row["CONSTRAINT_NAME"]), []).append(row)
        foreign_keys = []
        for (name, constraint), rows in grouped_fks.items():
            first = rows[0]
            fk = {
                "table": name,
                "name": constraint,
                "constrained_columns": [row["COLUMN_NAME"] for row in rows],
                "referred_schema": first["REFERENCED_TABLE_SCHEMA"],
                "referred_table": first["REFERENCED_TABLE_NAME"],
                "referred_columns": [row["REFERENCED_COLUMN_NAME"] for row in rows],
                "options": {"onupdate": first["UPDATE_RULE"], "ondelete": first["DELETE_RULE"]},
            }
            foreign_keys.append(fk)
            tables[name]["keys"].append({"kind": "foreign", "columns": fk["constrained_columns"], "name": constraint})
        definitions = {row["TABLE_NAME"]: row["VIEW_DEFINITION"] for row in views if row["VIEW_DEFINITION"]}
        warnings.extend(
            f"DEFINITION_UNAVAILABLE:{name}"
            for name, table in tables.items()
            if table["kind"] == "view" and name not in definitions
        )
        return [tables[name] for name in names], foreign_keys, definitions, warnings

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
