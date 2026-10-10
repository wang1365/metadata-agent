import pytest

from dsh.plugins.metadata_tools.capabilities import Capabilities
from dsh.plugins.metadata_tools.mysql import filter_objects


@pytest.fixture
def catalog(monkeypatch):
    class Database:
        described = []

        def __init__(self, _config):
            pass

        def objects(self):
            return [{"name": f"table_{i:04}", "kind": "table"} for i in range(750)] + [
                {"name": "Order_detail", "kind": "view"}
            ]

        def describe(self, names):
            self.described.append(names)
            return [], [], {}, []

        def close(self):
            pass

    monkeypatch.setattr("metadata_service.api.MySQL", Database)
    monkeypatch.setattr("dsh.plugins.metadata_tools.capabilities.MySQL", Database)
    return Database


def mysql_source(client):
    return client.post(
        "/api/v1/sources/mysql",
        json={
            "name": "test",
            "host": "placeholder",
            "database": "test",
            "username": "reader",
            "password": "placeholder",
        },
    ).json()["id"]


def test_filter_all_catalog_before_pagination(client, catalog):
    source = mysql_source(client)
    path = f"/api/v1/sources/{source}/objects"
    page = client.get(path, params={"query": "TABLE_07", "offset": 40, "limit": 20}).json()
    assert page["source_total"] == 751 and page["total"] == 50
    assert len(page["objects"]) == 10 and page["objects"][0]["name"] == "table_0740"
    page = client.get(path, params={"query": "^(order_|table_074[89]$)", "match": "regex"}).json()
    assert [item["name"] for item in page["objects"]] == ["Order_detail", "table_0748", "table_0749"]
    for params in ({"match": "regex", "query": "["}, {"query": "a" * 257}, {"match": "invalid"}):
        assert client.get(path, params=params).status_code == 400
    assert client.get(path, params={"offset": 750, "limit": 50}).json()["objects"][0]["name"] == "table_0749"


def test_explicit_validated_selection_and_retry_scope(client, store, catalog):
    source = mysql_source(client)
    for names, code in [
        ([], "TABLE_SELECTION_REQUIRED"),
        (["missing"], "UNKNOWN_TABLE_SELECTION"),
        (["table_0001"] * 2, "DUPLICATE_TABLE_SELECTION"),
    ]:
        response = client.post("/api/v1/analyses", json={"source_id": source, "tables": names})
        assert response.status_code == 400 and code in response.text
    assert not store.list("job")
    response = client.post("/api/v1/analyses", json={"source_id": source, "tables": ["table_0749", "Order_detail"]})
    assert response.status_code == 202
    job = response.json()["task_id"]
    store.claim(job)
    tools = Capabilities(store, job)
    assert {item["name"] for item in tools.list_objects()["objects"]} == {"table_0749", "Order_detail"}
    tools.describe_tables(["table_0749"])
    with pytest.raises(ValueError, match="TABLE_OUTSIDE_TASK"):
        tools.describe_tables(["table_0001"])
    assert catalog.described == [["table_0749"]]
    with pytest.raises(ValueError, match="TABLE_OUTSIDE_TASK"):
        tools.save_findings({"tables": [{"name": "table_0001", "columns": [{"name": "id"}]}]}, mode="merge")
    with pytest.raises(ValueError, match="TABLE_OUTSIDE_TASK"):
        tools.check_columns("table_0001", ["id"])
    with pytest.raises(ValueError, match="DEFINITION_OUTSIDE_TASK"):
        tools.get_sql_definition("routine", "procedure")
    store.update(job, status="failed")
    retried = client.post(f"/api/v1/analyses/{job}/retry").json()["task_id"]
    assert store.get(retried)["tables"] == ["table_0749", "Order_detail"]


def test_regex_timeout_and_literal_fuzzy():
    assert filter_objects([{"name": "a.b"}, {"name": "axb"}], ".", "fuzzy") == [{"name": "a.b"}]
    with pytest.raises(ValueError, match="REGEX_TIMEOUT"):
        filter_objects([{"name": "a" * 10000 + "!"}], "(a+)+$", "regex")
