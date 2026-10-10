import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager

from .config import ROOT, get_settings
from .store import Store


@contextmanager
def worker_lock(path):
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def stop_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def work(store, once=False):
    for job in store.list("job"):
        if job["status"] == "running":
            store.update(job["id"], status="failed", stage="执行中断，可显式重试", error="EXECUTION_INTERRUPTED")
    while True:
        queued = sorted([job for job in store.list("job") if job["status"] == "queued"], key=lambda j: j["created"])
        if not queued:
            if once:
                return
            time.sleep(1)
            continue
        job = store.claim(queued[0]["id"])
        folder = store.settings.job_dir(job["id"])
        folder.mkdir(parents=True, exist_ok=True)
        process = None
        with folder.joinpath("runner.log").open("a", encoding="utf-8") as log:
            try:
                env = os.environ.copy()
                env.update(
                    PYTHONPATH=str(ROOT), METADATA_DATA_DIR=str(store.settings.data_dir), PYTHONIOENCODING="utf-8"
                )
                process = subprocess.Popen(
                    [sys.executable, "-m", "metadata_service.runner", job["id"]],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
                started = time.monotonic()
                while process.poll() is None:
                    current = store.get(job["id"], "job")
                    if current["cancel_requested"] or time.monotonic() - started > store.settings.execution_timeout:
                        stop_tree(process)
                        store.update(
                            job["id"],
                            status="cancelled" if current["cancel_requested"] else "failed",
                            stage="已停止",
                            error=None if current["cancel_requested"] else "EXECUTION_TIMEOUT",
                        )
                        break
                    time.sleep(0.5)
                current = store.get(job["id"], "job")
                if current["status"] == "running":
                    store.update(
                        job["id"],
                        status="cancelled" if current["cancel_requested"] else "failed",
                        stage="执行结束",
                        error=None if current["cancel_requested"] else "EXECUTION_INTERRUPTED",
                    )
            finally:
                if process:
                    stop_tree(process)
        if once:
            return


if __name__ == "__main__":

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    settings = get_settings()
    try:
        with worker_lock(settings.data_dir / "worker.lock"):
            work(Store(settings), once="--once" in sys.argv)
    except KeyboardInterrupt:
        pass
