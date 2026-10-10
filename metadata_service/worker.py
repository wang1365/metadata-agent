import argparse
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import contextmanager
from threading import Event

from .config import ROOT, get_settings
from .store import Store, job_queue


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


def recover_queue(store, queue):
    for job in store.list("job"):
        if job["status"] == "running" and job_queue(job) == queue:
            store.update(job["id"], status="failed", stage="执行中断，可显式重试", error="EXECUTION_INTERRUPTED")
            store.log(job["id"], "task.interrupted", "所属队列 Worker 重启，原执行中断，可显式重试", level="error")


def work(store, once=False, queue="analysis"):
    recover_queue(store, queue)
    stop = Event()
    count = getattr(store.settings, queue + "_concurrency")
    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(run_slot, store, stop, once, queue) for _ in range(count)]
        try:
            while futures:
                done, _ = wait(futures, timeout=0.5, return_when=FIRST_EXCEPTION)
                for future in done:
                    future.result()
                futures = [future for future in futures if not future.done()]
        finally:
            stop.set()


def run_slot(store, stop, once, queue):
    while not stop.is_set():
        job = store.claim_next(queue)
        if job is None:
            if once:
                return
            stop.wait(1)
            continue
        store.log(job["id"], "task.started", "Worker 已领取任务，准备启动 DSH 子进程")
        cursor = 0

        def relay():
            nonlocal cursor
            while True:
                page = store.logs(job["id"], cursor)
                for item in page["events"]:
                    if item.get("origin_pid") != os.getpid():
                        print(json.dumps(item, ensure_ascii=False), file=sys.stderr, flush=True)
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break

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
                heartbeat = started
                while process.poll() is None:
                    relay()
                    current = store.get(job["id"], "job")
                    if stop.is_set():
                        stop_tree(process)
                        store.update(
                            job["id"], status="failed", stage="执行中断，可显式重试", error="EXECUTION_INTERRUPTED"
                        )
                        store.log(job["id"], "task.interrupted", "所属队列 Worker 已停止", level="error")
                        break
                    if current["cancel_requested"] or time.monotonic() - started > store.settings.execution_timeout:
                        stop_tree(process)
                        store.update(
                            job["id"],
                            status="cancelled" if current["cancel_requested"] else "failed",
                            stage="已停止",
                            error=None if current["cancel_requested"] else "EXECUTION_TIMEOUT",
                        )
                        store.log(
                            job["id"],
                            "task.stopped",
                            "任务已取消" if current["cancel_requested"] else "执行超过时限，已回收进程",
                            level="warning" if current["cancel_requested"] else "error",
                        )
                        break
                    if time.monotonic() - heartbeat >= 15:
                        store.log(
                            job["id"],
                            "worker.heartbeat",
                            f"执行进程仍在运行，已运行 {int(time.monotonic() - started)} 秒；等待 DSH 或工具事件",
                        )
                        heartbeat = time.monotonic()
                    time.sleep(0.5)
                current = store.get(job["id"], "job")
                if current["status"] == "running":
                    store.update(
                        job["id"],
                        status="cancelled" if current["cancel_requested"] else "failed",
                        stage="执行结束",
                        error=None if current["cancel_requested"] else "EXECUTION_INTERRUPTED",
                    )
                    store.log(job["id"], "task.interrupted", "运行进程退出，任务未正常交付结果", level="error")
            finally:
                if process:
                    stop_tree(process)
                relay()
        if once:
            return


def start_workers(env=None, once=False):
    processes = []
    try:
        for queue in ("analysis", "qa"):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-m", "metadata_service.worker", "--queue", queue] + (["--once"] if once else []),
                    cwd=ROOT,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
            )
        return processes
    except BaseException:
        for process in processes:
            stop_tree(process)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", choices=("all", "analysis", "qa"), default="all")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    settings = get_settings()
    if args.queue == "all":
        Store(settings)  # Initialize SQLite before queue processes start together.
        processes = start_workers(once=args.once)
        try:
            while any(process.poll() is None for process in processes):
                if not args.once and any(process.poll() is not None for process in processes):
                    raise RuntimeError("Queue worker exited")
                time.sleep(0.5)
            if any(process.returncode for process in processes):
                raise RuntimeError("Queue worker failed")
        finally:
            for process in processes:
                stop_tree(process)
    else:
        with worker_lock(settings.data_dir / f"worker-{args.queue}.lock"):
            work(Store(settings), once=args.once, queue=args.queue)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
