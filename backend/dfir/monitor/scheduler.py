"""Continuous incident monitoring: re-check known URLs, look for new archive snapshots,
re-run enabled discovery adapters, and raise alerts on change."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import select

from ..core.db import case_dir, session, vault_for
from ..core.models import Alert, Location, Media, MonitorConfig

_scheduler: BackgroundScheduler | None = None


def run_monitor(case_id: str, progress=lambda f, m: None) -> dict:
    from ..pipeline import run_genealogy
    from ..propagation.capture import run_capture
    from ..propagation.intake import enrich

    alerts = []
    with session() as s:
        locs = [l for l in s.exec(select(Location).where(Location.case_id == case_id)).all()]
        before_media = {m.sha256 for m in s.exec(select(Media).where(Media.case_id == case_id)).all()}
        for i, loc in enumerate(locs):
            progress(i / max(1, len(locs)) * 0.8, f"Re-checking {loc.url[:60]}")
            prev = loc.state
            res = run_capture(s, case_id, loc, actor="monitor")
            new = res.get("state")
            if prev in ("live", "changed") and new == "removed":
                alerts.append(Alert(case_id=case_id, kind="removed", message=f"Now unavailable: {loc.url}",
                                    data={"location_id": loc.id, "previous": prev}))
            elif prev == "removed" and new in ("live", "changed"):
                alerts.append(Alert(case_id=case_id, kind="restored", message=f"Back online: {loc.url}",
                                    data={"location_id": loc.id}))
            elif new == "changed":
                alerts.append(Alert(case_id=case_id, kind="changed", message=f"Page content changed: {loc.url}",
                                    data={"location_id": loc.id}))
            elif new == "error" and prev != "error":
                alerts.append(Alert(case_id=case_id, kind="error", message=f"Could not re-check: {loc.url}",
                                    data={"location_id": loc.id, "error": res.get("error", "")[:200]}))
        after = s.exec(select(Media).where(Media.case_id == case_id)).all()
        new_media = [m for m in after if m.sha256 not in before_media]
        for m in new_media:
            alerts.append(Alert(case_id=case_id, kind="new_copy",
                                message=f"New/altered media version found: {m.label}", data={"media_id": m.id}))
        # archive: invalidate the cached Wayback lookups so new snapshots surface
        p = case_dir(case_id) / "enrichment.json"
        old_counts = {}
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            old_counts = {u: len(v) for u, v in data.get("wayback", {}).items() if isinstance(v, list)}
            data["wayback"] = {}
            p.write_text(json.dumps(data), encoding="utf-8")
        progress(0.85, "Checking archives")
        enr = enrich(case_id)
        for u, snaps in enr.get("wayback", {}).items():
            if isinstance(snaps, list) and len(snaps) > old_counts.get(u, 0) and u in old_counts:
                alerts.append(Alert(case_id=case_id, kind="new_snapshot",
                                    message=f"New Internet Archive snapshot(s) of {u}", data={"url": u}))
        for a in alerts:
            s.add(a)
        cfg = s.get(MonitorConfig, case_id) or MonitorConfig(case_id=case_id)
        cfg.last_run_at = datetime.now(timezone.utc)
        s.add(cfg)
        s.commit()
        vault_for(case_id).custody.append("monitor.run", actor="monitor",
                                          details={"alerts": [a.kind for a in alerts], "locations": len(locs)})
    if new_media:
        progress(0.9, "Updating genealogy with new versions")
        run_genealogy(case_id)
    progress(1.0, "Monitoring check complete")
    return {"alerts": len(alerts)}


def _tick():
    now = datetime.now(timezone.utc)
    with session() as s:
        cfgs = s.exec(select(MonitorConfig).where(MonitorConfig.enabled == True)).all()  # noqa: E712
    from ..core import jobs
    from ..core.models import Job
    with session() as s:
        busy = {j.case_id for j in s.exec(select(Job).where(Job.kind == "monitor", Job.status.in_(("queued", "running")))).all()}
    for c in cfgs:
        if c.case_id in busy:
            continue
        last = c.last_run_at.replace(tzinfo=timezone.utc) if c.last_run_at and c.last_run_at.tzinfo is None else c.last_run_at
        if last is None or (now - last).total_seconds() >= c.interval_minutes * 60:
            jobs.submit("monitor", c.case_id, lambda p, cid=c.case_id: run_monitor(cid, p))


def start() -> None:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(_tick, "interval", minutes=1, id="monitor_tick", max_instances=1, coalesce=True)
        _scheduler.start()


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
