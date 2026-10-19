from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..core import jobs
from ..core.db import get_session, load_claims, vault_for
from ..core.models import AnalysisRun, Artifact, Case, Location, Media
from ..pipeline import analyze_media, ingest_media, media_explanation, run_genealogy
from ..safety.gate import SafetyPolicy

router = APIRouter(prefix="/api")


class CaseIn(BaseModel):
    title: str = ""
    incident_type: str = "other"
    description: str = ""
    safety: dict = {}
    complainant: dict = {}
    # link-first start: the first URL is the suspect photo/video/post; the rest are other places it was seen
    urls: list[str] = []
    find_copies: bool = True
    depth: int = 1


class CasePatch(BaseModel):
    title: str | None = None
    incident_type: str | None = None
    description: str | None = None
    safety: dict | None = None
    complainant: dict | None = None
    status: str | None = None


def _case(s: Session, case_id: str) -> Case:
    c = s.get(Case, case_id)
    if not c:
        raise HTTPException(404, "case not found")
    return c


def thumbs(s: Session, case_id: str) -> dict[str, str]:
    return {a.media_id: a.sha256 for a in s.exec(select(Artifact).where(Artifact.case_id == case_id,
                                                                        Artifact.kind == "thumb")).all()}


@router.get("/cases")
def list_cases(s: Session = Depends(get_session)):
    out = []
    for c in s.exec(select(Case).order_by(Case.created_at.desc())).all():
        n_media = s.exec(select(func.count()).select_from(Media).where(Media.case_id == c.id)).one()
        n_loc = s.exec(select(func.count()).select_from(Location).where(Location.case_id == c.id)).one()
        if c.status == "lab":
            continue
        out.append({**c.model_dump(mode="json", exclude={"complainant"}), "media_count": n_media, "location_count": n_loc})
    return out


@router.post("/cases")
def create_case(body: CaseIn, s: Session = Depends(get_session)):
    from urllib.parse import urlparse

    from .workflow import start_investigation
    urls = [u.strip() for u in body.urls if u and u.strip()]
    fields = body.model_dump(exclude={"urls", "find_copies", "depth"})
    if not fields["title"].strip():
        host = urlparse(urls[0] if "//" in urls[0] else "//" + urls[0]).hostname if urls else None
        fields["title"] = f"Suspected fake from {host}" if host else "Untitled incident"
    c = Case(**fields)
    s.add(c)
    s.commit()
    vault_for(c.id).custody.append("case.opened", actor="investigator",
                                   details={"title": c.title, "incident_type": c.incident_type, "links": len(urls)})
    out = c.model_dump(mode="json")
    if urls:
        out["job"] = start_investigation(s, c.id, urls, body.find_copies, body.depth).model_dump(mode="json")
    return out


@router.get("/cases/{case_id}")
def get_case(case_id: str, s: Session = Depends(get_session)):
    c = _case(s, case_id)
    th = thumbs(s, case_id)
    media = s.exec(select(Media).where(Media.case_id == case_id).order_by(Media.created_at)).all()
    return {
        "case": c.model_dump(mode="json"),
        "safety_notices": SafetyPolicy.from_case(c.safety).notices(),
        "blur": SafetyPolicy.from_case(c.safety).blur_previews,
        "media": [_media_row(s, case_id, m, th) for m in media],
    }


def _media_row(s: Session, case_id: str, m: Media, th: dict) -> dict:
    row = {**m.model_dump(mode="json", exclude={"fingerprints"}), "thumb": th.get(m.id), "level": None, "verdict": None}
    if m.fingerprints:
        ex = media_explanation(s, case_id, m.id)
        row["level"] = ex["level"]
        v = ex["verdict"]
        if v.get("available"):
            row["verdict"] = v
        else:
            row["verdict_note"] = (
                "No detector could judge this video here. The built-in AI and face-swap detectors only check photos; "
                "Hive (set a key in .env) can check videos. The forensic findings are still in 'Is it fake?'."
                if m.modality == "video" else
                "No detector could judge this file. Audio deepfakes are not covered yet; use the forensic findings and, "
                "for a voice, verify by calling the person back on a number you already know."
                if m.modality == "audio" else v.get("detail") or v.get("headline"))
    return row


@router.patch("/cases/{case_id}")
def patch_case(case_id: str, body: CasePatch, s: Session = Depends(get_session)):
    c = _case(s, case_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    s.add(c)
    s.commit()
    vault_for(case_id).custody.append("case.updated", actor="investigator",
                                      details={"fields": list(body.model_dump(exclude_none=True).keys())})
    return c


@router.post("/cases/{case_id}/media")
async def upload_media(case_id: str, file: UploadFile = File(...), role: str = Form("primary"),
                       label: str = Form(""), s: Session = Depends(get_session)):
    _case(s, case_id)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    try:
        m = ingest_media(s, case_id, data, file.filename or "upload", role=role, label=label,
                         acquisition={"method": "user upload via investigation UI", "original_filename": file.filename,
                                      "content_type": file.content_type})
    except ValueError as e:
        raise HTTPException(415, str(e)) from e

    def work(p):
        analyze_media(case_id, m.id, lambda f, msg: p(f * 0.8, msg))
        p(0.85, "Comparing with other media in the case")
        run_genealogy(case_id)
        p(1.0, "done")

    job = jobs.submit("analyze", case_id, work, target=m.id)
    return {"media": m.model_dump(mode="json", exclude={"fingerprints"}), "job": job.model_dump(mode="json")}


@router.get("/cases/{case_id}/media/{media_id}")
def media_detail(case_id: str, media_id: str, s: Session = Depends(get_session)):
    m = s.get(Media, media_id)
    if not m or m.case_id != case_id:
        raise HTTPException(404)
    arts = s.exec(select(Artifact).where(Artifact.media_id == media_id)).all()
    runs = s.exec(select(AnalysisRun).where(AnalysisRun.media_id == media_id)).all()
    loc = s.get(Location, m.location_id) if m.location_id else None
    fp = m.fingerprints or {}
    return {
        "media": m.model_dump(mode="json", exclude={"fingerprints"}),
        "fingerprint_summary": {"pdq": (fp.get("image") or fp.get("video", {}).get("rep") or {}).get("pdq"),
                                "frames": len(fp.get("video", {}).get("frames", [])), "audio": "audio" in fp,
                                "jpeg_q": fp.get("jpeg_q"), "device": fp.get("device"),
                                "encoder_signature": fp.get("encoder_signature")},
        "location": loc.model_dump(mode="json") if loc else None,
        "claims": [c.model_dump(mode="json") for c in load_claims(s, case_id, "decomposition", media_id) +
                   [c for c in load_claims(s, case_id, "genealogy", media_id) if c.category == "family_provenance"]],
        "artifacts": [a.model_dump(mode="json") for a in arts],
        "runs": [r.model_dump(mode="json") for r in runs],
        "explanation": media_explanation(s, case_id, media_id),
    }


@router.post("/cases/{case_id}/media/{media_id}/analyze")
def reanalyze(case_id: str, media_id: str, only: str | None = None, s: Session = Depends(get_session)):
    _case(s, case_id)
    only_set = set(only.split(",")) if only else None
    job = jobs.submit("analyze", case_id, lambda p: analyze_media(case_id, media_id, p, only=only_set), target=media_id)
    return job


@router.get("/vault/{case_id}/{sha256}")
def vault_file(case_id: str, sha256: str, download: bool = False):
    v = vault_for(case_id)
    if len(sha256) != 64 or not all(ch in "0123456789abcdef" for ch in sha256) or not v.exists(sha256):
        raise HTTPException(404)
    rec = v.record(sha256) or {}
    return FileResponse(v.path(sha256), media_type=rec.get("mime") or "application/octet-stream",
                        filename=rec.get("filename") if download else None)


@router.get("/cases/{case_id}/analysis-status")
def analysis_status(case_id: str, s: Session = Depends(get_session)):
    """What is running right now for this case, step by step, with an ETA from past run times."""
    from statistics import median

    from ..analyzers.base import registry
    from ..core.models import Job
    active = s.exec(select(Job).where(Job.case_id == case_id, Job.status.in_(("queued", "running")))
                    .order_by(Job.created_at)).all()
    out = []
    now = datetime.now(timezone.utc)
    for j in active:
        item = {"job_id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress, "message": j.message,
                "created_at": j.created_at.isoformat(),
                "elapsed_s": round((now - _aware(j.started_at or j.created_at)).total_seconds(), 1), "steps": []}
        m = s.get(Media, j.target) if j.target else None
        if m and j.kind == "analyze":
            item["media"] = {"id": m.id, "label": m.label, "modality": m.modality}
            started = _aware(j.started_at or j.created_at)
            runs = {r.component: r for r in s.exec(select(AnalysisRun).where(AnalysisRun.media_id == m.id)).all()
                    if r.finished_at and _aware(r.finished_at) >= started}
            remaining = 0.0
            for a in registry():
                if not a.applies(m):
                    continue
                hist = [(_aware(r.finished_at) - _aware(r.started_at)).total_seconds() for r in s.exec(
                    select(AnalysisRun).where(AnalysisRun.component == a.name, AnalysisRun.outcome == "ok")
                    .order_by(AnalysisRun.started_at.desc()).limit(15)).all() if r.finished_at]
                typical = round(median(hist), 1) if hist else None
                r = runs.get(a.name)
                if r:
                    st = {"ok": "done", "not_run": "not_checked"}.get(r.outcome, "error")
                    dur = round((_aware(r.finished_at) - _aware(r.started_at)).total_seconds(), 1)
                elif j.status == "running" and j.message == (a.title or a.name):
                    st, dur = "running", None
                else:
                    st, dur = "pending", None
                if st in ("running", "pending") and a.available() is None:
                    remaining += typical or 2.0
                item["steps"].append({"name": a.name, "title": a.title or a.name, "status": st, "duration_s": dur,
                                      "typical_s": typical})
            item["eta_s"] = round(remaining)
        out.append(item)
    return {"active": out}


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)
