"""Forensic modules: listing, results per file, re-running, candidate devices, validation results, and UI preferences."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..core import jobs, prefs
from ..core.db import get_session, session, vault_for
from ..core.models import Media
from ..forensics import devices, registry, runner
from ..forensics.base import VALIDATION_FILE, validation_data
from .cases import _case

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------------------------------------ modules
@router.get("/forensics/modules")
def modules():
    disabled = set(prefs.load()["forensics"]["disabled_modules"])
    mods = [{**m, "enabled": m["id"] not in disabled} for m in registry.describe()]
    return {"modules": mods, "plugins": registry.plugin_report(), "plugin_dir": str(registry.plugin_dir()),
            "entry_point_group": registry.ENTRY_POINT_GROUP}


@router.get("/forensics/validation")
def validation():
    d = validation_data()
    return d or {"modules": {}, "note": f"No validation results found at {VALIDATION_FILE.name}. Run scripts/forensics_bench.py."}


class ModuleToggle(BaseModel):
    enabled: bool


@router.put("/forensics/modules/{module_id}")
def toggle_module(module_id: str, body: ModuleToggle):
    if registry.get(module_id) is None:
        raise HTTPException(404, "unknown module")
    cur = prefs.load()["forensics"]["disabled_modules"]
    new = [m for m in cur if m != module_id] + ([] if body.enabled else [module_id])
    prefs.save({"forensics": {"disabled_modules": sorted(set(new))}})
    return modules()


# ------------------------------------------------------------------------------------------------ per-case results
@router.get("/cases/{case_id}/forensics")
def case_forensics(case_id: str, media_id: str | None = None, s: Session = Depends(get_session)):
    _case(s, case_id)
    q = select(Media).where(Media.case_id == case_id)
    if media_id:
        q = q.where(Media.id == media_id)
    ms = s.exec(q.order_by(Media.created_at)).all()
    out = []
    for m in ms:
        applicable = [x.id for x in registry.all_modules() if x.applies(m.modality)]
        out.append({"media_id": m.id, "label": m.label, "role": m.role, "modality": m.modality, "width": m.width, "height": m.height,
                    "applicable": applicable, "results": runner.latest_results(s, case_id, m.id)})
    return {"media": out, "devices": devices.list_devices(case_id)}


class RunIn(BaseModel):
    media_id: str
    modules: list[str] | None = None


@router.post("/cases/{case_id}/forensics/run")
def run_forensics(case_id: str, body: RunIn, s: Session = Depends(get_session)):
    _case(s, case_id)
    m = s.get(Media, body.media_id)
    if not m or m.case_id != case_id:
        raise HTTPException(404, "media not in this case")
    ids = set(body.modules) if body.modules else None
    if ids and not ids <= {x.id for x in registry.all_modules()}:
        raise HTTPException(400, "unknown module id")
    return jobs.submit("forensics", case_id, lambda p: runner.rerun(case_id, body.media_id, ids, p), target=body.media_id)


# ------------------------------------------------------------------------------------------------ candidate devices
class DeviceIn(BaseModel):
    name: str
    reference_media_ids: list[str]
    note: str = ""


@router.get("/cases/{case_id}/devices")
def list_devices(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return devices.list_devices(case_id)


@router.post("/cases/{case_id}/devices")
def create_device(case_id: str, body: DeviceIn, s: Session = Depends(get_session)):
    _case(s, case_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Give the candidate device a name (for example 'Suspect phone, serial ending 4821').")
    refs = []
    for mid in body.reference_media_ids:
        m = s.get(Media, mid)
        if not m or m.case_id != case_id or m.modality != "image":
            raise HTTPException(400, "Every reference must be an image in this case.")
        refs.append({"id": m.id, "sha256": m.sha256, "label": m.label})
    if len(refs) < 5:
        raise HTTPException(400, "At least 5 reference photos from the suspected camera are needed (20 or more is better).")

    def work(p):
        p(0.1, "Extracting sensor noise from the reference photos")
        dev = devices.create_device(case_id, name, refs, body.note)
        p(1.0, "Fingerprint saved" + ("" if dev["coherent"] else " - but the references do not share a consistent pattern"))

    return jobs.submit("fingerprint", case_id, work)


@router.delete("/cases/{case_id}/devices/{device_id}")
def delete_device(case_id: str, device_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    if not devices.delete_device(case_id, device_id):
        raise HTTPException(404, "no such device")
    return {"ok": True}


# ------------------------------------------------------------------------------------------------ preferences
class PrefsIn(BaseModel):
    theme: str | None = None
    mode: str | None = None
    accent: str | None = None
    motion: str | None = None
    profile: dict | None = None


@router.get("/preferences")
def get_prefs():
    return prefs.load()


@router.put("/preferences")
def put_prefs(body: PrefsIn):
    upd = body.model_dump(exclude_none=True)
    bad = {"theme": prefs.THEMES, "mode": prefs.MODES, "motion": prefs.MOTION}
    for k, allowed in bad.items():
        if k in upd and upd[k] not in allowed:
            raise HTTPException(400, f"{k} must be one of {', '.join(allowed)}")
    if "accent" in upd and not prefs.HEX.match(upd["accent"]):
        raise HTTPException(400, "accent must be a colour like #2f6f9f")
    return prefs.save(upd)
