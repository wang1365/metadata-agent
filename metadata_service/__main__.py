"""Local API + independent Worker launcher."""

import os
import subprocess
import sys

import uvicorn

from .config import ROOT, get_settings
from .worker import stop_tree


def main():
    settings = get_settings()
    env = os.environ.copy()
    env.update(METADATA_DATA_DIR=str(settings.data_dir), PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    worker = subprocess.Popen(
        [sys.executable, "-m", "metadata_service.worker"],
        cwd=ROOT,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    try:
        uvicorn.run("metadata_service.api:create_app", factory=True, host="127.0.0.1", port=8000)
    finally:
        stop_tree(worker)


if __name__ == "__main__":
    main()
