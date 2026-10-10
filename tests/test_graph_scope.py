def test_graph_defaults_to_all_tables_including_isolated_and_later_pages(client, store):
    tables = [{"name": f"table_{index}", "columns": []} for index in range(500)]
    relations = [{"source_table": "table_0", "target_table": f"table_{index}"} for index in range(1, 202)]
    snapshot = store.create(
        "snapshot",
        {"findings": {"tables": tables, "relationships": relations, "lineage": []}},
    )
    graph = client.get(f"/api/v1/snapshots/{snapshot}/graph").json()
    assert graph["tables"] == tables and graph["root"] is None
    assert len(graph["edges"]) == 200 and graph["total_edges"] == 201 and graph["edges_truncated"]
    hub = client.get(f"/api/v1/snapshots/{snapshot}/graph?root=table_0").json()
    assert len(hub["tables"]) == 202 and hub["edges_truncated"]
    focused = client.get(f"/api/v1/snapshots/{snapshot}/graph?root=table_1").json()
    assert {table["name"] for table in focused["tables"]} == {"table_0", "table_1"}
    assert len(focused["edges"]) == 1 and not focused["edges_truncated"]
    isolated = client.get(f"/api/v1/snapshots/{snapshot}/graph?root=table_499").json()
    assert isolated["tables"] == [tables[-1]] and isolated["edges"] == []
    lineage = client.get(f"/api/v1/snapshots/{snapshot}/graph?kind=lineage").json()
    assert lineage["tables"] == tables and lineage["edges"] == []
    assert client.get(f"/api/v1/snapshots/{snapshot}/graph?root=missing").status_code == 400


def test_empty_snapshot_graph(client, store):
    snapshot = store.create("snapshot", {"findings": {"tables": [], "relationships": [], "lineage": []}})
    response = client.get(f"/api/v1/snapshots/{snapshot}/graph")
    assert response.status_code == 200
    assert response.json() == {"tables": [], "edges": [], "root": None, "total_edges": 0, "edges_truncated": False}
