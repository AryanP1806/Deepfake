"""Minimal background job runner with progress events for SSE streaming."""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from .db import session
from .models import Job

log = logging.getLogger("dfir.jobs")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dfir-job")
_subscribers: dict[str, list[queue.Queue]] = {}
_sub_lock = threading.Lock()


def _publish(case_id: str | None, event: dict) -> None:
    with _sub_lock:
        for q in _subscribers.get(case_id or "*", []) + _subscribers.get("*", []):
            q.put(event)


def subscribe(case_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _sub_lock:
        _subscribers.setdefault(case_id, []).append(q)
    return q


def unsubscribe(case_id: str, q: queue.Queue) -> None:
    with _sub_lock:
        if q in _subscribers.get(case_id, []):
            _subscribers[case_id].remove(q)


class Progress:
    def __init__(self, job_id: str, case_id: str | None):
        self.job_id = job_id
        self.case_id = case_id

    def __call__(self, frac: float, message: str) -> None:
        try:
            with session() as s:
                j = s.get(Job, self.job_id)
                if j:
                    j.progress = max(0.0, min(1.0, frac))
                    j.message = message
                    s.add(j)
                    s.commit()
        except Exception:  # noqa: BLE001 — progress is best-effort; never fail the job over it
            pass
        _publish(self.case_id, {"type": "job", "job_id": self.job_id, "progress": frac, "message": message})


def submit(kind: str, case_id: str | None, fn: Callable[[Progress], None], target: str | None = None) -> Job:
    with session() as s:
        job = Job(kind=kind, case_id=case_id, target=target)
        s.add(job)
        s.commit()
        s.refresh(job)

    def run():
        prog = Progress(job.id, case_id)
        with session() as s:
            j = s.get(Job, job.id)
            j.status = "running"
            j.started_at = datetime.now(timezone.utc)
            s.add(j)
            s.commit()
        _publish(case_id, {"type": "job", "job_id": job.id, "kind": kind, "status": "running"})
        log.info("job %s (%s) started for %s", job.id, kind, case_id)
        t0 = datetime.now(timezone.utc)
        try:
            fn(prog)
            status, err = "done", ""
        except Exception as e:  # noqa: BLE001
            status, err = "error", f"{e}\n{traceback.format_exc()}"
            log.error("job %s (%s) failed: %s", job.id, kind, e)
        if status == "done":
            log.info("job %s (%s) done in %.1fs", job.id, kind, (datetime.now(timezone.utc) - t0).total_seconds())
        with session() as s:
            j = s.get(Job, job.id)
            j.status = status
            j.error = err[-4000:]
            j.progress = 1.0 if status == "done" else j.progress
            j.finished_at = datetime.now(timezone.utc)
            s.add(j)
            s.commit()
        _publish(case_id, {"type": "job", "job_id": job.id, "kind": kind, "status": status,
                           "error": err.splitlines()[0] if err else ""})

    _executor.submit(run)
    return job


def run_sync(kind: str, case_id: str | None, fn: Callable[[Progress], None]) -> None:
    """Run a job inline (used by tests and the demo builder)."""
    with session() as s:
        job = Job(kind=kind, case_id=case_id, status="running")
        s.add(job)
        s.commit()
        s.refresh(job)
    fn(Progress(job.id, case_id))
    with session() as s:
        j = s.get(Job, job.id)
        j.status = "done"
        j.progress = 1.0
        j.finished_at = datetime.now(timezone.utc)
        s.add(j)
        s.commit()


async def event_stream(case_id: str):
    q = subscribe(case_id)
    try:
        while True:
            try:
                ev = await asyncio.get_event_loop().run_in_executor(None, q.get, True, 15)
                yield ev
            except queue.Empty:
                yield {"type": "ping"}
    finally:
        unsubscribe(case_id, q)
