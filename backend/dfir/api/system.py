from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from ..core import consent, jobs
from ..core.config import KNOWN_KEYS, key_status, set_key
from ..core.db import get_session
from ..core.model_store import SPECS, status
from ..core.models import Alert, Consent, Job, MonitorConfig
from ..monitor.scheduler import run_monitor
from .cases import _case

router = APIRouter(prefix="/api")


@router.get("/settings")
def settings():
    from ..analyzers.base import registry
    analyzers = []
    for a in registry():
        nr = a.available()
        analyzers.append({"name": a.name, "title": a.title, "modalities": a.modalities, "available": nr is None,
                          "reason": nr.reason if nr else "", "how_to_enable": nr.how_to_enable if nr else ""})
    return {"keys": key_status(), "models": [status(k) for k in SPECS], "analyzers": analyzers,
            "outward_actions": consent.OUTWARD_ACTIONS}


class KeyIn(BaseModel):
    name: str
    value: str | None = None


@router.put("/settings/keys")
def put_key(body: KeyIn):
    if body.name not in KNOWN_KEYS:
        raise HTTPException(400, "unknown key")
    set_key(body.name, body.value)
    return key_status()


class ConsentIn(BaseModel):
    action: str
    case_id: str | None = None
    scope: str = "once"
    detail: dict = {}


@router.post("/consent")
def grant_consent(body: ConsentIn, s: Session = Depends(get_session)):
    try:
        c = consent.grant(s, body.action, body.case_id, body.scope, body.detail)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.case_id:
        from ..core.db import vault_for
        vault_for(body.case_id).custody.append("consent.granted", actor="investigator",
                                               details={"action": body.action, "scope": body.scope})
    return c


@router.get("/cases/{case_id}/consents")
def list_consents(case_id: str, s: Session = Depends(get_session)):
    return s.exec(select(Consent).where(Consent.case_id == case_id)).all()


@router.get("/cases/{case_id}/jobs")
def case_jobs(case_id: str, s: Session = Depends(get_session)):
    return s.exec(select(Job).where(Job.case_id == case_id).order_by(Job.created_at.desc()).limit(30)).all()


@router.get("/jobs/{job_id}")
def job(job_id: str, s: Session = Depends(get_session)):
    j = s.get(Job, job_id)
    if not j:
        raise HTTPException(404)
    return j


@router.get("/cases/{case_id}/events")
async def events(case_id: str):
    async def gen():
        async for ev in jobs.event_stream(case_id):
            yield {"event": ev.get("type", "message"), "data": json.dumps(ev)}
    return EventSourceResponse(gen())


class MonitorIn(BaseModel):
    enabled: bool
    interval_minutes: int = 360


@router.get("/cases/{case_id}/monitor")
def get_monitor(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    cfg = s.get(MonitorConfig, case_id) or MonitorConfig(case_id=case_id)
    alerts = s.exec(select(Alert).where(Alert.case_id == case_id).order_by(Alert.created_at.desc())).all()
    return {"config": cfg, "alerts": alerts}


@router.put("/cases/{case_id}/monitor")
def put_monitor(case_id: str, body: MonitorIn, s: Session = Depends(get_session)):
    _case(s, case_id)
    cfg = s.get(MonitorConfig, case_id) or MonitorConfig(case_id=case_id)
    cfg.enabled, cfg.interval_minutes = body.enabled, max(15, body.interval_minutes)
    s.add(cfg)
    s.commit()
    return cfg


@router.post("/cases/{case_id}/monitor/run")
def monitor_now(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return jobs.submit("monitor", case_id, lambda p: run_monitor(case_id, p))


@router.post("/cases/{case_id}/alerts/seen")
def alerts_seen(case_id: str, s: Session = Depends(get_session)):
    for a in s.exec(select(Alert).where(Alert.case_id == case_id, Alert.seen == False)).all():  # noqa: E712
        a.seen = True
        s.add(a)
    s.commit()
    return {"ok": True}
