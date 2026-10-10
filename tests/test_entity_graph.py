def fixture_snapshot(store, count=500, relations=5001):
    tables = [
        {
            "name": f"table_{i:03d}",
            "kind": "view" if i % 5 == 0 else "table",
            "comment": "synthetic",
            "columns": [{"name": "id"}],
            "evidence_ids": [],
        }
        for i in range(count)
    ]
    relationships = [
        {
            "id": f"r{i:05d}",
            "source_table": f"table_{i % (count - 1):03d}",
            "target_table": f"table_{(i + 1) % (count - 1):03d}",
            "source_columns": ["id"],
            "target_columns": ["id"],
            "origin": "inferred" if i == relations - 1 else "constraint",
            "confidence": "high",
            "review": "pending",
            "evidence_ids": ["evidence"],
        }
        for i in range(relations)
    ]
    return store.create(
        "snapshot",
        {
            "findings": {
                "tables": tables,
                "relationships": relationships,
                "lineage": [{"source_table": "table_000", "target_table": "table_001"}],
            }
        },
    )


def test_entity_graph_budget_filter_before_cutoff_and_exact_detail(client, store):
    snap = fixture_snapshot(store)
    path = f"/api/v1/snapshots/{snap}"
    data = client.get(path + "/entity-graph").json()
    assert len(data["nodes"]) == 500 and len(data["relationships"]) == 5000 and data["truncated"]
    assert data["total_relationships"] == 5001 and data["returned_relationships"] == 5000
    assert data["nodes"][-1]["id"] == "table_499" and "columns" not in data["nodes"][0]
    filtered = client.get(path + "/entity-graph?origin=inferred&confidence=high").json()
    assert len(filtered["relationships"]) == 1 and filtered["relationships"][0]["id"] == "r05000"
    assert not filtered["truncated"] and len(filtered["nodes"]) == 500
    assert client.get(path + "/entity-graph?origin=").json()["relationships"] == []
    assert client.get(path + "/entity-graph?root=table_499").json()["nodes"] == [data["nodes"][-1]]
    detail = client.get(path + "/tables/table_499").json()
    assert detail["name"] == "table_499" and detail["columns"] == [{"name": "id"}]
    assert client.get(path + "/tables/table_49").status_code == 404
    assert client.get(path + "/entity-graph?root=unknown").status_code == 400
    assert client.get(path + "/entity-graph?review=unknown").status_code == 400
    assert client.get(path + "/entity-graph?limit=5001").status_code == 422
    assert len(client.get(path + "/graph").json()["edges"]) == 200


def test_entity_graph_review_self_multi_and_scope_nodes_before_truncation(client, store):
    snap = fixture_snapshot(store, 5, 8)
    record = store.get(snap, "snapshot")
    edges = record["findings"]["relationships"]
    edges[0].update(target_table=edges[0]["source_table"], source_columns=["id", "type"], target_columns=["id", "type"])
    edges[1]["review"] = "rejected"
    store.update(snap, **{"findings": record["findings"]})
    path = f"/api/v1/snapshots/{snap}/entity-graph"
    default = client.get(path).json()
    assert len(default["relationships"]) == 7 and default["relationships"][0]["source_columns"] == ["id", "type"]
    rejected = client.get(path + "?review=rejected").json()
    assert [edge["id"] for edge in rejected["relationships"]] == ["r00001"]
    centered = client.get(path + "?root=table_001&limit=1&review=pending&review=rejected").json()
    assert {node["id"] for node in centered["nodes"]} == {"table_000", "table_001", "table_002"}
    assert centered["truncated"] and len(centered["relationships"]) == 1
    assert store.list("job") == []


def test_entity_graph_empty(client, store):
    snap = store.create("snapshot", {"findings": {"tables": [], "relationships": [], "lineage": []}})
    data = client.get(f"/api/v1/snapshots/{snap}/entity-graph").json()
    assert data["nodes"] == data["relationships"] == [] and not data["truncated"] and data["root"] is None
