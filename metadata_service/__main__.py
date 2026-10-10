"""Local API + independent Worker launcher."""

import os

import uvicorn

from .config import ROOT, get_settings
from .store import Store
from .worker import start_workers, stop_tree


def main():
    settings = get_settings()
    Store(settings)
    env = os.environ.copy()
    env.update(METADATA_DATA_DIR=str(settings.data_dir), PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    workers = start_workers(env)
    try:
        uvicorn.run("metadata_service.api:create_app", factory=True, host="127.0.0.1", port=8000)
    finally:
        for worker in workers:
            stop_tree(worker)


if __name__ == "__main__":
    main()
