"""Verify documentation through real Vite/API HTTP servers, without DSH or a database."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from metadata_service.config import ROOT
from metadata_service.store import uid
from metadata_service.worker import stop_tree


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def main():
    api_port, ui_port = free_port(), free_port()
    while ui_port == api_port:
        ui_port = free_port()
    folder = ROOT / "data" / ("api-docs-smoke-" + uid())
    folder.mkdir(parents=True)
    env = dict(os.environ, METADATA_DATA_DIR=str(folder), METADATA_API_PORT=str(api_port), PYTHONIOENCODING="utf-8")
    node = shutil.which("node")
    if not node or not (ROOT / "frontend/node_modules/vite/bin/vite.js").exists():
        raise RuntimeError("Install frontend dependencies with npm ci before running this check")
    commands = [
        (
            [sys.executable, "-m", "uvicorn", "metadata_service.api:create_app", "--factory", "--port", str(api_port)],
            ROOT,
        ),
        (
            [node, "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(ui_port), "--strictPort"],
            ROOT / "frontend",
        ),
    ]
    processes = []
    with (folder / "servers.log").open("w", encoding="utf-8") as log:
        try:
            for command, cwd in commands:
                processes.append(
                    subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=env,
                        stdout=log,
                        stderr=log,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                )
            base = f"http://127.0.0.1:{ui_port}"
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urlopen(base + "/api/v1/health", timeout=2) as response:
                        assert json.load(response)["status"] == "ok"
                    break
                except Exception:
                    if time.monotonic() >= deadline or any(process.poll() is not None for process in processes):
                        raise RuntimeError(f"Servers failed to start; inspect {folder / 'servers.log'}") from None
                    time.sleep(0.2)
            for path in ("/api/v1/docs", "/docs", "/redoc"):
                with urlopen(base + path, timeout=5) as response:
                    page = response.read().decode()
                    assert "SwaggerUIBundle" in page and "/api/v1/openapi.json" in page
                    assert '<div id="root">' not in page  # Vite's SPA fallback is the original bug.
            for path in ("/api/v1/openapi.json", "/openapi.json"):
                with urlopen(base + path, timeout=5) as response:
                    assert "/api/v1/analyses" in json.load(response)["paths"]
            for name in ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png"):
                with urlopen(base + "/api/v1/docs/assets/" + name, timeout=5) as response:
                    assert response.status == 200 and response.read()
            print("API_DOCS_PROXY_OK canonical=200 legacy=200 schema=200 assets=200 custom_ports=true", flush=True)
        finally:
            for process in reversed(processes):
                stop_tree(process)


if __name__ == "__main__":
    main()
