"""Supervise local development processes; never runs an agent loop."""

import os
import signal
import subprocess
import sys
import time
from contextlib import ExitStack

from .config import ROOT, get_settings
from .worker import stop_tree


def main():
    settings = get_settings()
    api_port = int(os.getenv("METADATA_API_PORT", "8000"))
    ui_port = int(os.getenv("METADATA_UI_PORT", "5173"))
    if not (1 <= api_port <= 65535 and 1 <= ui_port <= 65535) or api_port == ui_port:
        raise ValueError("Choose different valid API and frontend ports")
    env = os.environ.copy()
    env.update(METADATA_DATA_DIR=str(settings.data_dir), METADATA_API_PORT=str(api_port), PYTHONIOENCODING="utf-8")
    logs = settings.data_dir / "dev-logs"
    logs.mkdir(exist_ok=True)
    commands = [
        ("worker", [sys.executable, "-m", "metadata_service.worker"]),
        (
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "metadata_service.api:create_app",
                "--factory",
                "--reload",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
        ),
        (
            "frontend",
            ["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(ui_port), "--strictPort"],
        ),
    ]
    processes = []

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    with ExitStack() as stack:
        try:
            for name, command in commands:
                log = stack.enter_context((logs / (name + ".log")).open("a", encoding="utf-8"))
                process = subprocess.Popen(
                    command,
                    cwd=ROOT / "frontend" if name == "frontend" else ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
                processes.append((name, process))
            print(
                f"Frontend: http://127.0.0.1:{ui_port}\nREST API: http://127.0.0.1:{api_port}/docs\nLogs: {logs}\nStop: Ctrl+C",
                flush=True,
            )
            while True:
                for name, process in processes:
                    if process.poll() is not None:
                        raise RuntimeError(f"{name} exited ({process.returncode}); inspect {logs / (name + '.log')}")
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            for _name, process in processes:
                stop_tree(process)


if __name__ == "__main__":
    main()
