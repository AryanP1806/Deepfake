"""Home dashboard and the global (all-cases) views: recent cases with verdicts, system status, search, vault, monitoring, reports.

Everything here is a read-only aggregation of data the per-case endpoints already own; nothing new is stored.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlmodel import Session, select

from ..core.config import APP_VERSION, MODELS_DIR
from ..core.db import case_dir, get_session, vault_for
from ..core.models import Alert, Case, Complaint, Job, Location, MonitorConfig, Media, Report
from ..pipeline import media_explanation
from .cases import thumbs
from .lab import STARTED, _gpu

router = APIRouter(prefix="/api")

ACTIVE = ("queued", "running")


def _visible_cases(s: Session, archived: bool | None = False) -> list[Case]:
    rows = s.exec(select(Case).order_by(Case.created_at.desc())).all()
    rows = [c for c in rows if c.status != "lab"]
    if archived is None:
        return rows
    return [c for c in rows if (c.status == "archived") == archived]


def _case_summary(s: Session, c: Case, active_cases: set[str]) -> dict:
    media = s.exec(select(Media).where(Media.case_id == c.id).order_by(Media.created_at)).all()
    primary = next((m for m in media if m.role == "primary"), media[0] if media else None)
    th = thumbs(s, c.id)
    verdict = None
    if primary is not None and primary.fingerprints:
        try:
            v = media_explanation(s, c.id, primary.id)["verdict"]
            if v.get("available"):
                verdict = {"label": v["label"], "p_fake": v["p_fake"], "headline": v.get("headline")}
        except Exception:  # noqa: BLE001
            verdict = None
    n_loc = s.exec(select(func.count()).select_from(Location).where(Location.case_id == c.id, Location.dismissed == False)).one()  # noqa: E712
    return {"id": c.id, "title": c.title, "incident_type": c.incident_type, "created_at": c.created_at.isoformat(), "status": c.status,
            "is_demo": c.is_demo, "sensitive": bool(c.safety.get("intimate") or c.safety.get("minors_possible")),
            "media_count": len(media), "location_count": n_loc, "modality": primary.modality if primary else None,
            "thumb": th.get(primary.id) if primary else None, "verdict": verdict, "investigating": c.id in active_cases}


def _models() -> list[dict]:
    from ..analyzers import asr, clip_backend
    from ..core.gpu import models
    from ..core.model_store import SPECS, status
    loaded = {m["key"]: m for m in models.status()}
    out = []
    clip, whisper = clip_backend.runtime_info(), asr.runtime_info()
    for key, title, rt in (("clip", "CLIP model", clip), ("whisper", "Whisper model", whisper)):
        installed = status("ufd")["installed"] if key == "clip" and "ufd" in SPECS else (MODELS_DIR / "whisper" / "ready").exists()
        if rt.get("loaded"):
            prec = (rt.get("precision") or ("int8" if key == "whisper" else "")).upper()
            out.append({"key": key, "title": title, "state": "loaded", "detail": f"Loaded ({prec})" if prec else "Loaded",
                        "mb": loaded.get(key, {}).get("est_mb"), "device": rt.get("device")})
        else:
            out.append({"key": key, "title": title, "state": "idle" if installed else "missing",
                        "detail": "Ready (loads on first use)" if installed else "Not installed", "mb": None, "device": None})
    return out


@router.get("/dashboard")
def dashboard(limit: int = 6, s: Session = Depends(get_session)):
    active = {j.case_id for j in s.exec(select(Job).where(Job.status.in_(ACTIVE))).all() if j.case_id}
    cases = _visible_cases(s, archived=False)
    alerts = s.exec(select(Alert).where(Alert.seen == False).order_by(Alert.created_at.desc())).all()  # noqa: E712
    titles = {c.id: c.title for c in cases}
    gpu = _gpu()
    gpu_ok = bool(gpu.get("cuda_available"))
    return {
        "recent": [_case_summary(s, c, active) for c in cases[:limit]],
        "counts": {"cases": len(cases), "alerts": len(alerts), "active_jobs": len(active)},
        "alerts": [{"id": a.id, "case_id": a.case_id, "case_title": titles.get(a.case_id, a.case_id), "kind": a.kind, "message": a.message,
                    "created_at": a.created_at.isoformat()} for a in alerts[:8]],
        "system": {"ok": True, "backend": {"running": True, "version": APP_VERSION, "uptime_s": round(time.time() - STARTED)},
                   "gpu": {"available": gpu_ok, "name": gpu.get("name"), "vram_total_mb": gpu.get("vram_total_mb"), "vram_used_mb": gpu.get("vram_used_mb"),
                           "utilization_pct": gpu.get("utilization_pct")},
                   "models": _models()},
    }


@router.get("/cases-summary")
def cases_summary(archived: bool = False, s: Session = Depends(get_session)):
    """Case rows for the Cases page: the same shape as the dashboard's recent list, for every case."""
    active = {j.case_id for j in s.exec(select(Job).where(Job.status.in_(ACTIVE))).all() if j.case_id}
    return [_case_summary(s, c, active) for c in _visible_cases(s, archived=archived)]


@router.get("/search")
def search(q: str = Query("", max_length=200), s: Session = Depends(get_session)):
    q = q.strip()
    if len(q) < 2:
        return {"query": q, "results": []}
    like = f"%{q}%"
    cases = {c.id: c for c in _visible_cases(s, archived=None)}
    out: list[dict] = []
    for c in s.exec(select(Case).where(or_(Case.title.ilike(like), Case.description.ilike(like), Case.id.ilike(like)))).all():
        if c.id in cases:
            out.append({"type": "Case", "title": c.title, "subtitle": c.id, "case_id": c.id, "route": f"/case/{c.id}/overview"})
    for m in s.exec(select(Media).where(or_(Media.filename.ilike(like), Media.label.ilike(like), Media.sha256.ilike(f"{q}%"), Media.md5.ilike(f"{q}%")))).all():
        if m.case_id in cases:
            out.append({"type": "Media", "title": m.label or m.filename, "subtitle": f"{cases[m.case_id].title} · sha256 {m.sha256[:12]}…",
                        "case_id": m.case_id, "route": f"/case/{m.case_id}/decomposition/{m.id}"})
    for l in s.exec(select(Location).where(or_(Location.url.ilike(like), Location.domain.ilike(like), Location.account_handle.ilike(like), Location.caption.ilike(like)))).all():
        if l.case_id in cases:
            out.append({"type": "Place", "title": l.url, "subtitle": f"{cases[l.case_id].title} · {l.platform or l.domain}",
                        "case_id": l.case_id, "route": f"/case/{l.case_id}/spread"})
    return {"query": q, "results": out[:14], "total": len(out)}


@router.get("/vault-overview")
def vault_overview(s: Session = Depends(get_session)):
    rows = []
    for c in _visible_cases(s, archived=None):
        try:
            v = vault_for(c.id)
            recs = v.all_records()
            entries = v.custody.entries()
        except Exception:  # noqa: BLE001
            recs, entries = [], []
        exports = sorted((case_dir(c.id) / "exports").glob("*.zip")) if (case_dir(c.id) / "exports").exists() else []
        rows.append({"case_id": c.id, "title": c.title, "status": c.status, "items": len(recs), "bytes": sum(int(r.get("size") or 0) for r in recs),
                     "custody_entries": len(entries), "last_custody": entries[-1].get("ts_utc") if entries else None, "exports": len(exports)})
    return {"cases": rows, "totals": {"items": sum(r["items"] for r in rows), "bytes": sum(r["bytes"] for r in rows),
                                      "custody_entries": sum(r["custody_entries"] for r in rows)}}


@router.get("/monitoring-overview")
def monitoring_overview(s: Session = Depends(get_session)):
    cfgs = {m.case_id: m for m in s.exec(select(MonitorConfig)).all()}
    alerts = s.exec(select(Alert).order_by(Alert.created_at.desc())).all()
    rows = []
    for c in _visible_cases(s, archived=False):
        cfg = cfgs.get(c.id)
        mine = [a for a in alerts if a.case_id == c.id]
        rows.append({"case_id": c.id, "title": c.title, "enabled": bool(cfg and cfg.enabled), "interval_minutes": cfg.interval_minutes if cfg else None,
                     "last_run_at": cfg.last_run_at.isoformat() if cfg and cfg.last_run_at else None,
                     "unseen": sum(1 for a in mine if not a.seen), "alerts": len(mine),
                     "places": s.exec(select(func.count()).select_from(Location).where(Location.case_id == c.id, Location.dismissed == False)).one()})  # noqa: E712
    titles = {r["case_id"]: r["title"] for r in rows}
    return {"cases": rows, "alerts": [{"id": a.id, "case_id": a.case_id, "case_title": titles.get(a.case_id, a.case_id), "kind": a.kind, "message": a.message,
                                       "seen": a.seen, "created_at": a.created_at.isoformat()} for a in alerts[:40] if a.case_id in titles]}


@router.get("/reports-overview")
def reports_overview(s: Session = Depends(get_session)):
    comps = {c.case_id: c for c in s.exec(select(Complaint)).all()}
    rows = []
    for c in _visible_cases(s, archived=None):
        comp = comps.get(c.id)
        n_reports = s.exec(select(func.count()).select_from(Report).where(Report.case_id == c.id)).one()
        exports = sorted((case_dir(c.id) / "exports").glob("*.zip")) if (case_dir(c.id) / "exports").exists() else []
        rows.append({"case_id": c.id, "title": c.title, "status": c.status, "complaint_state": comp.state if comp else "not started",
                     "ack_number": comp.ack_number if comp else "", "takedown_reports": n_reports,
                     "exports": [{"name": p.name, "size": p.stat().st_size} for p in exports][-3:]})
    return {"cases": rows}
