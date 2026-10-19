"""Device/source report, PRNU comparison, generator attribution and the lead engine."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..core import jobs
from ..core.db import get_session, load_claims, session, vault_for
from ..core.models import Case, MatchEdge, Media
from ..forensics import prnu
from ..pipeline import media_explanation
from ..reasoning.generator import generator_attribution
from ..reasoning.lead_engine import build_lead_engine
from ..reasoning.source import all_prnu, device_report, save_prnu
from .cases import _case

router = APIRouter(prefix="/api")


def _media(s: Session, case_id: str) -> list[Media]:
    ms = s.exec(select(Media).where(Media.case_id == case_id).order_by(Media.created_at)).all()
    return sorted([m for m in ms if m.fingerprints], key=lambda m: (m.role != "primary", m.created_at))[:16]


# ----------------------------------------------------------------------------------------------- device / source
@router.get("/cases/{case_id}/source")
def source(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    ms = _media(s, case_id)
    refs = [{"id": m.id, "label": m.label, "width": m.width, "height": m.height} for m in
            s.exec(select(Media).where(Media.case_id == case_id, Media.role == "reference")).all() if m.modality == "image"]
    return {"media": [device_report(s, case_id, m) for m in ms], "reference_images": refs,
            "prnu_requirements": {"min_references": prnu.MIN_REFERENCES, "same_pixel_size": True, "pce_match": prnu.PCE_MATCH,
                                  "limits": prnu.LIMITS}}


class PrnuIn(BaseModel):
    target_media_id: str
    reference_media_ids: list[str] = []  # photos known to come from the suspected camera
    compare_with: str | None = None  # or: do these two questioned files share a sensor?


def _same_picture(a: Media, b: Media) -> bool:
    """Two files showing the same picture (copies, crops, re-saves). Their noise correlates because of the PICTURE,
    which would look like a sensor match but is not one, so such pairs must never be compared."""
    if a.sha256 == b.sha256:
        return True
    from ..genealogy.match import PDQ_LOOSE, hamming_hex
    pa, pb = (a.fingerprints or {}).get("image"), (b.fingerprints or {}).get("image")
    if pa and pb:
        return min(hamming_hex(pa["pdq"], pb["pdq"]), hamming_hex(pa["pdq"], pb["pdq_mirror"])) <= PDQ_LOOSE
    return False


@router.post("/cases/{case_id}/prnu")
def run_prnu(case_id: str, body: PrnuIn, s: Session = Depends(get_session)):
    _case(s, case_id)
    t = s.get(Media, body.target_media_id)
    if not t or t.case_id != case_id or t.modality != "image":
        raise HTTPException(400, "Choose an image from this case as the questioned file.")
    if body.compare_with:
        o = s.get(Media, body.compare_with)
        if not o or o.case_id != case_id:
            raise HTTPException(400, "The other file is not in this case.")
        if _same_picture(t, o) or s.exec(select(MatchEdge).where(MatchEdge.case_id == case_id, MatchEdge.verified == True,  # noqa: E712
                                                                 MatchEdge.a_media.in_([t.id, o.id]), MatchEdge.b_media.in_([t.id, o.id]))).first():
            raise HTTPException(400, "These two files show the same picture (one is a copy of the other), so their noise would match because of the picture itself, "
                                     "not because of the camera. Compare files that show DIFFERENT scenes.")
    else:  # photos that show the same picture as the questioned file would inflate the match: leave them out
        keep = [i for i in body.reference_media_ids if (m := s.get(Media, i)) and not _same_picture(t, m)]
        if len(keep) != len(body.reference_media_ids):
            body.reference_media_ids = keep
    if not body.compare_with and len(body.reference_media_ids) < prnu.MIN_REFERENCES:
        raise HTTPException(400, f"Add at least {prnu.MIN_REFERENCES} reference photos from the suspected camera (use 'Add authentic reference'), "
                                 "all with exactly the same pixel size as the questioned image.")

    def work(p):
        v = vault_for(case_id)
        with session() as s2:
            tm = s2.get(Media, body.target_media_id)
            p(0.1, "Extracting sensor noise")
            if body.compare_with:
                om = s2.get(Media, body.compare_with)
                res = {"kind": "pair", "pair": [tm.id, om.id], **prnu.compare_pair(v.path(tm.sha256), v.path(om.sha256))}
                key = f"pair_{om.id}"
            else:
                refs = [s2.get(Media, i) for i in body.reference_media_ids[:30]]
                res = {"kind": "reference", "references": [r.label for r in refs if r],
                       **prnu.compare_to_references([v.path(r.sha256) for r in refs if r], v.path(tm.sha256))}
                key = "ref"
            res.update(target=tm.id, target_label=tm.label, at=datetime.now(timezone.utc).isoformat())
        save_prnu(case_id, body.target_media_id, key, res)
        v.custody.append("analysis.prnu", actor="investigator", details={"target": body.target_media_id, "status": res.get("status"), "pce": res.get("pce")})
        p(1.0, f"Sensor-noise comparison: {res.get('status')}")

    return jobs.submit("prnu", case_id, work, target=body.target_media_id)


@router.get("/cases/{case_id}/prnu")
def prnu_results(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return {"results": all_prnu(case_id)}


# ----------------------------------------------------------------------------------------------- generator
@router.get("/cases/{case_id}/generator")
def generator(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    out = []
    for m in _media(s, case_id):
        claims = load_claims(s, case_id, "decomposition", m.id) + [c for c in load_claims(s, case_id, "genealogy", m.id) if c.category == "family_provenance"]
        p_ai = None
        try:
            p_ai = (media_explanation(s, case_id, m.id)["verdict"] or {}).get("p_ai")
        except Exception:  # noqa: BLE001
            pass
        out.append({"media_id": m.id, "label": m.label, "role": m.role, "modality": m.modality, **generator_attribution(claims, p_ai)})
    return {"media": out}


# ----------------------------------------------------------------------------------------------- lead engine
@router.get("/cases/{case_id}/lead-engine")
def lead_engine(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return build_lead_engine(s, case_id)
