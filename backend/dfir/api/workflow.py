"""Link-first investigation, spread map, takedown tracker and case deletion."""
from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..core import jobs
from ..core.config import DATA_DIR
from ..core.db import case_dir, get_session, vault_for
from ..core.models import (Alert, AnalysisRun, Artifact, Case, ClaimRow, Complaint, Consent, Job, Location, MatchEdge,
                           Media, MonitorConfig, Report, TimelineNote)
from ..investigate import load_state, run_investigation
from ..propagation.discover import configured_providers
from ..propagation.intake import add_locations
from ..propagation.spread import build_spread
from ..reporting.tracker import build_plan, escalation_text, upsert_report, view
from .cases import _case

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------------------------------------ investigation
class InvestigateIn(BaseModel):
    urls: list[str] = []
    find_copies: bool = True  # allow search services to receive the image / caption (never auto-allowed for intimate cases)
    depth: int = 1


def start_investigation(s: Session, case_id: str, urls: list[str], find_copies: bool, depth: int) -> Job:
    """Add the given links (the first one is the 'seed' when the case has no suspect media yet) and run the pipeline."""
    has_primary = s.exec(select(Media).where(Media.case_id == case_id, Media.role == "primary")).first() is not None
    has_seed = s.exec(select(Location).where(Location.case_id == case_id, Location.seed == True)).first() is not None  # noqa: E712
    items = []
    for i, u in enumerate(u.strip() for u in urls if u and u.strip()):
        items.append({"url": u, "seed": i == 0 and not has_primary and not has_seed})
    add_locations(s, case_id, items)
    depth = max(0, min(3, depth))
    return jobs.submit("investigate", case_id, lambda p: run_investigation(case_id, depth=depth, find_copies=find_copies, progress=p))


@router.post("/cases/{case_id}/investigate")
def investigate(case_id: str, body: InvestigateIn, s: Session = Depends(get_session)):
    _case(s, case_id)
    st = load_state(case_id)
    if st and st.get("running"):
        raise HTTPException(409, "An investigation is already running for this case")
    return start_investigation(s, case_id, body.urls, body.find_copies, body.depth)


@router.get("/cases/{case_id}/investigation")
def investigation_state(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    st = load_state(case_id)
    if st and st.get("running"):  # a crashed/restarted backend must not show "running" forever
        active = s.exec(select(Job).where(Job.case_id == case_id, Job.kind == "investigate",
                                          Job.status.in_(("queued", "running")))).first()
        if not active:
            st["running"] = False
            st["error"] = st.get("error") or "The investigation was interrupted (the backend restarted). Run it again."
    return {"state": st, "providers": configured_providers()}


@router.get("/cases/{case_id}/spread")
def spread(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return build_spread(s, case_id)


class DismissIn(BaseModel):
    dismissed: bool = True


@router.post("/cases/{case_id}/locations/{loc_id}/dismiss")
def dismiss_location(case_id: str, loc_id: str, body: DismissIn, s: Session = Depends(get_session)):
    l = s.get(Location, loc_id)
    if not l or l.case_id != case_id:
        raise HTTPException(404)
    l.dismissed = body.dismissed
    s.add(l)
    s.commit()
    vault_for(case_id).custody.append("location.dismissed" if body.dismissed else "location.included", actor="investigator",
                                      details={"url": l.url, "location_id": l.id})
    return {"id": l.id, "dismissed": l.dismissed}


# ------------------------------------------------------------------------------------------------ takedown tracker
@router.get("/cases/{case_id}/takedown")
def takedown(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return build_plan(s, case_id)


class ReportIn(BaseModel):
    key: str
    kind: str | None = None  # None = keep what is stored ("other" for a brand-new report)
    title: str | None = None
    target: str | None = None
    status: str | None = None
    reference: str | None = None
    notes: str | None = None
    note: str | None = None
    submitted_at: str | None = None
    due_at: str | None = None
    location_ids: list[str] | None = None


@router.post("/cases/{case_id}/reports")
def record_report(case_id: str, body: ReportIn, s: Session = Depends(get_session)):
    _case(s, case_id)
    return view(upsert_report(s, case_id, body.model_dump(exclude_none=True)))


@router.delete("/cases/{case_id}/reports/{report_id}")
def delete_report(case_id: str, report_id: str, s: Session = Depends(get_session)):
    r = s.get(Report, report_id)
    if not r or r.case_id != case_id:
        raise HTTPException(404)
    s.delete(r)
    s.commit()
    vault_for(case_id).custody.append("report.deleted_by_user", actor="investigator", details={"key": r.key})
    return {"deleted": report_id}


@router.get("/cases/{case_id}/reports/{report_id}/escalation")
def escalation(case_id: str, report_id: str, s: Session = Depends(get_session)):
    r = s.get(Report, report_id)
    case = _case(s, case_id)
    if not r or r.case_id != case_id:
        raise HTTPException(404)
    urls = [l.url for l in (s.get(Location, i) for i in (r.location_ids or [])) if l]
    return {"text": escalation_text(case, r, urls)}


# ------------------------------------------------------------------------------------------------ delete
def _rm_readonly(func, path, _exc):
    """The evidence vault stores files read-only; clear that bit so the folder can be removed."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


@router.get("/cases/{case_id}/deletion-preview")
def deletion_preview(case_id: str, s: Session = Depends(get_session)):
    c = _case(s, case_id)
    n = lambda model: len(s.exec(select(model).where(model.case_id == case_id)).all())  # noqa: E731
    d = case_dir(case_id)
    size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0
    exports = list((d / "exports").glob("*.zip")) if (d / "exports").exists() else []
    return {"title": c.title, "media": n(Media), "locations": n(Location), "reports": n(Report), "claims": n(ClaimRow),
            "megabytes": round(size / 1e6, 1), "exports": [p.name for p in exports],
            "active_jobs": len(s.exec(select(Job).where(Job.case_id == case_id, Job.status.in_(("queued", "running")))).all())}


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, confirm: str = "", s: Session = Depends(get_session)):
    """Permanently remove a case: database rows AND the evidence vault and custody log. Requires confirm=DELETE."""
    c = _case(s, case_id)
    if confirm != "DELETE":
        raise HTTPException(400, "Type DELETE to confirm. This permanently removes the evidence and cannot be undone.")
    if s.exec(select(Job).where(Job.case_id == case_id, Job.status.in_(("queued", "running")))).first():
        raise HTTPException(409, "Something is still running for this case. Wait for it to finish, then delete.")
    v = vault_for(case_id)
    head = None
    try:
        ent = v.custody.entries()
        head = ent[-1].get("entry_hash") if ent else None
    except Exception:  # noqa: BLE001
        pass
    counts = {}
    for model in (Media, ClaimRow, Artifact, AnalysisRun, Location, MatchEdge, TimelineNote, Consent, Job, Complaint,
                  MonitorConfig, Alert, Report):
        rows = s.exec(select(model).where(model.case_id == case_id)).all()
        counts[model.__name__] = len(rows)
        for r in rows:
            s.delete(r)
    s.delete(c)
    s.commit()
    shutil.rmtree(case_dir(case_id), onerror=_rm_readonly)
    # a minimal deletion record outlives the case (what was deleted, when, and the final custody-chain hash)
    log = DATA_DIR / "deleted_cases.jsonl"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"case_id": case_id, "title": c.title, "deleted_at": datetime.now(timezone.utc).isoformat(),
                             "counts": counts, "final_custody_hash": head}) + "\n")
    return {"deleted": case_id, "counts": counts}
