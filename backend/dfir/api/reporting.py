from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..core import jobs
from ..core.custody import verify_chain
from ..core.db import case_dir, get_session, session, vault_for
from ..core.models import Complaint, TimelineNote
from ..reporting.complaint import complaint_docx, get_or_create, legal_refs, platform_routes
from ..reporting.package import export_package
from .cases import _case

router = APIRouter(prefix="/api")


@router.get("/cases/{case_id}/evidence")
def evidence(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    v = vault_for(case_id)
    lines = (case_dir(case_id) / "custody_log.jsonl").read_text(encoding="utf-8").splitlines() \
        if (case_dir(case_id) / "custody_log.jsonl").exists() else []
    ok, problems = verify_chain(lines)
    exports = sorted((case_dir(case_id) / "exports").glob("*.zip")) if (case_dir(case_id) / "exports").exists() else []
    return {"records": v.all_records(), "custody": {"entries": v.custody.entries()[-500:], "chain_ok": ok,
                                                    "problems": problems, "count": len(lines)},
            "exports": [{"name": p.name, "size": p.stat().st_size} for p in exports]}


@router.post("/cases/{case_id}/evidence/verify")
def verify_vault(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    v = vault_for(case_id)
    bad = [r["sha256"] for r in v.all_records() if not v.verify(r["sha256"])]
    v.custody.append("vault.verify", actor="investigator", details={"items": len(v.all_records()), "failures": bad})
    return {"items": len(v.all_records()), "failures": bad}


@router.post("/cases/{case_id}/export")
def export(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)

    def work(p):
        p(0.1, "Assembling evidence package")
        with session() as s2:
            path = export_package(s2, case_id)
        p(1.0, f"Package ready: {path.name}")

    return jobs.submit("export", case_id, work)


@router.get("/cases/{case_id}/exports/{name}")
def download_export(case_id: str, name: str):
    p = case_dir(case_id) / "exports" / name
    if "/" in name or "\\" in name or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/zip", filename=name)


@router.get("/cases/{case_id}/complaint")
def complaint(case_id: str, regenerate: bool = False, s: Session = Depends(get_session)):
    _case(s, case_id)
    c = get_or_create(s, case_id, regenerate=regenerate)
    return c


class ComplaintEdit(BaseModel):
    edits: dict


@router.put("/cases/{case_id}/complaint")
def edit_complaint(case_id: str, body: ComplaintEdit, s: Session = Depends(get_session)):
    c = get_or_create(s, case_id)
    if c.state == "submitted":
        raise HTTPException(409, "already marked as submitted")
    f = dict(c.fields)
    f["user_edits"] = {**(f.get("user_edits") or {}), **body.edits}
    c.fields = f
    c.state = "edited"
    c.updated_at = datetime.now(timezone.utc)
    s.add(c)
    s.commit()
    return c


class Submitted(BaseModel):
    ack_number: str
    route: str = "NCRP"
    submitted_at: str | None = None


@router.post("/cases/{case_id}/complaint/submitted")
def mark_submitted(case_id: str, body: Submitted, s: Session = Depends(get_session)):
    """Record that the USER submitted the complaint themselves (the system never submits)."""
    c = get_or_create(s, case_id)
    when = datetime.fromisoformat(body.submitted_at) if body.submitted_at else datetime.now(timezone.utc)
    c.state, c.ack_number, c.submitted_at = "submitted", body.ack_number, when
    s.add(c)
    s.commit()
    from ..reporting.tracker import upsert_report  # keeps the follow-up tracker in step (also writes the timeline entry)
    upsert_report(s, case_id, {"key": "ncrp", "kind": "ncrp", "title": "NCRP complaint", "target": body.route,
                               "status": "submitted", "reference": body.ack_number, "submitted_at": when.isoformat()})
    vault_for(case_id).custody.append("report.submitted_by_user", actor="investigator",
                                      details={"route": body.route, "ack_number": body.ack_number})
    return c


@router.get("/cases/{case_id}/complaint.docx")
def complaint_doc(case_id: str, s: Session = Depends(get_session)):
    case = _case(s, case_id)
    c = get_or_create(s, case_id)
    return Response(complaint_docx(c.fields, case.title),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="complaint_{case_id}.docx"'})


class NoteIn(BaseModel):
    title: str
    detail: str = ""
    kind: str = "user_note"
    at: str | None = None


@router.post("/cases/{case_id}/notes")
def add_note(case_id: str, body: NoteIn, s: Session = Depends(get_session)):
    _case(s, case_id)
    n = TimelineNote(case_id=case_id, at=datetime.fromisoformat(body.at) if body.at else datetime.now(timezone.utc),
                     title=body.title, detail=body.detail, kind=body.kind)
    s.add(n)
    s.commit()
    vault_for(case_id).custody.append("note.logged", actor="investigator", details={"title": body.title, "kind": body.kind})
    return n


@router.get("/legal")
def legal():
    return {"legal": legal_refs(), "routes": platform_routes()}
