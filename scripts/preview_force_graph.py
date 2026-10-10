"""Serve synthetic graph fixtures locally; never starts DSH or reads a business database."""

import argparse
from pathlib import Path

import uvicorn

from metadata_service.api import create_app
from metadata_service.config import ROOT, Settings
from metadata_service.store import Store, uid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    settings = Settings(data_dir=ROOT / "data" / ("force-preview-" + uid())).initialize()
    store = Store(settings)
    source = store.create(
        "source", {"type": "excel", "name": "合成力导图验收（500表/5000关系）", "sheets": [], "size": 0}
    )
    tables = [
        {
            "name": f"table_{i:03d}",
            "kind": "view" if i % 5 == 0 else "table",
            "comment": "合成测试实体，无业务数据",
            "columns": [{"name": "id", "type": "bigint", "comment": "测试主键", "evidence_ids": ["synthetic"]}],
            "keys": [{"kind": "primary", "columns": ["id"], "name": "PRIMARY"}],
            "evidence_ids": ["synthetic"],
        }
        for i in range(500)
    ]
    relationships = [
        {
            "id": f"r{i:05d}",
            "source_table": f"table_{i % 499:03d}",
            "target_table": f"table_{(i % 499 + i // 499 + 1) % 499:03d}",
            "source_columns": ["id"],
            "target_columns": ["id"],
            "origin": ("constraint", "dictionary", "inferred")[i % 3],
            "confidence": ("high", "medium", "low")[i % 3],
            "review": "confirmed" if i % 7 == 0 else "pending",
            "explanation": "合成验收关系",
            "evidence_ids": ["synthetic"],
        }
        for i in range(5000)
    ]
    store.create(
        "snapshot",
        {
            "source_id": source,
            "findings": {"tables": tables, "relationships": relationships, "lineage": [], "warnings": []},
            "evidence": {"synthetic": {"kind": "synthetic", "content": "仅用于验证图交互，无业务数据"}},
            "reviews": [],
        },
    )
    print(f"SYNTHETIC_FORCE_PREVIEW http://127.0.0.1:{args.port} data={Path(settings.data_dir).name}", flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
