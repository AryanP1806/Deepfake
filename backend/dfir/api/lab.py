"""Lab: a simple view of the backend — system/GPU/model status, live logs, jobs — and a quick
"drop a file, see every analyzer's raw output" bench."""
from __future__ import annotations

import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from ..analyzers.media_io import ffmpeg_version
from ..core import jobs, logbuf
from ..core.config import APP_VERSION, DATA_DIR, MODELS_DIR
from ..core.db import get_session, load_claims, vault_for
from ..core.model_store import SPECS, status
from ..core.models import AnalysisRun, Artifact, Case, Job, Media
from ..pipeline import analyze_media, ingest_media, media_explanation

router = APIRouter(prefix="/api")
STARTED = time.time()


def _gpu() -> dict:
    info: dict = {"torch": None, "cuda_available": False}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            info.update(name=torch.cuda.get_device_name(0), capability=".".join(map(str, torch.cuda.get_device_capability(0))),
                        cuda_runtime=torch.version.cuda, vram_total_mb=round(total / 2**20),
                        vram_used_mb=round((total - free) / 2**20),
                        torch_allocated_mb=round(torch.cuda.memory_allocated() / 2**20),
                        torch_reserved_mb=round(torch.cuda.memory_reserved() / 2**20))
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)[:200]
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=utilization.gpu,temperature.gpu,clocks.sm,pstate,driver_version",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
            u, t, clk, ps, drv = [x.strip() for x in out.strip().split(",")[:5]]
            info.update(utilization_pct=u, temperature_c=t, sm_clock_mhz=clk, pstate=ps, driver=drv)
        except Exception:  # noqa: BLE001
            pass
    return info


_disk_cache: dict = {}


def _disk_usage() -> tuple[int, int]:
    """Directory sizes, cached for 60 s (walking the vault on every 3 s poll is wasteful)."""
    if time.time() - _disk_cache.get("t", 0) > 60:
        size = lambda d: sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.exists() else 0  # noqa: E731
        _disk_cache.update(t=time.time(), data=size(DATA_DIR), models=size(MODELS_DIR))
    return _disk_cache["data"], _disk_cache["models"]


@router.get("/system")
def system(s: Session = Depends(get_session)):
    from ..analyzers import asr, clip_backend
    from ..analyzers.base import registry
    analyzers = []
    for a in registry():
        nr = a.available()
        analyzers.append({"name": a.name, "title": a.title, "version": a.version, "modalities": a.modalities,
                          "order": a.order, "available": nr is None, "reason": nr.reason if nr else "",
                          "how_to_enable": nr.how_to_enable if nr else ""})
    recent = s.exec(select(Job).order_by(Job.created_at.desc()).limit(25)).all()
    disk, models_disk = _disk_usage()
    return {
        "app_version": APP_VERSION, "uptime_s": round(time.time() - STARTED), "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}", "ffmpeg": ffmpeg_version(),
        "gpu": _gpu(),
        "runtime": {"clip": clip_backend.runtime_info(), "whisper": asr.runtime_info()},
        "gpu_models": __import__("dfir.core.gpu", fromlist=["models"]).models.status(),
        "models": [{**status(k), "used_by": SPECS[k].used_by} for k in SPECS] + [{
            "key": "whisper", "title": "faster-whisper small (speech transcription)",
            "installed": (MODELS_DIR / "whisper" / "ready").exists(), "size": "~480 MB",
            "trained_on": "OpenAI Whisper multilingual corpus", "blind_spots": "Names, code-mixed speech, noisy audio.",
            "licence": "MIT", "files": {}, "used_by": "asr"}],
        "analyzers": analyzers,
        "jobs": [j.model_dump(mode="json") for j in recent],
        "storage": {"data_dir": str(DATA_DIR), "data_mb": round(disk / 2**20, 1), "models_dir": str(MODELS_DIR),
                    "models_mb": round(models_disk / 2**20, 1)},
    }


@router.get("/system/logs")
def logs(after: int = 0, level: str | None = None, limit: int = 300):
    return logbuf.tail(after, limit, level)


@router.post("/system/warmup")
def warmup():
    """Load the image detectors onto the GPU now, so the first analysis isn't slowed by model loading."""
    return jobs.submit("warmup", None, lambda p: warm_models(p))


def warm_models(p=lambda f, m: None) -> None:
    from ..analyzers import research_detectors as rd
    from ..core.gpu import models
    steps = [("SPAI (AI-image detector)", "spai", "spai.pth", rd._load_spai, 1500),
             ("TruFor (edit detector)", "trufor", "TruFor_weights.zip", rd._load_trufor, 1200),
             ("SBI (face-swap detector)", "sbi", "FFc23.tar", rd._load_sbi, 500)]
    for i, (title, key, fn, loader, mb) in enumerate(steps):
        p(i / len(steps), f"Loading {title}")
        if rd._need(key, fn) is None:
            models.get(key, loader, est_mb=mb)
    p(1.0, "Models resident")


@router.post("/lab/analyze")
async def lab_analyze(file: UploadFile = File(...), s: Session = Depends(get_session)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    case = Case(title=f"Lab: {file.filename}", status="lab", description="Quick analysis from the Lab bench")
    s.add(case)
    s.commit()
    vault_for(case.id).custody.append("case.opened", actor="investigator", details={"lab": True})
    try:
        m = ingest_media(s, case.id, data, file.filename or "upload",
                         acquisition={"method": "lab bench upload", "original_filename": file.filename})
    except ValueError as e:
        raise HTTPException(415, str(e)) from e
    job = jobs.submit("analyze", case.id, lambda p: analyze_media(case.id, m.id, p), target=m.id)
    return {"case_id": case.id, "media_id": m.id, "job_id": job.id}


@router.get("/lab/runs")
def lab_runs(s: Session = Depends(get_session)):
    cases = s.exec(select(Case).where(Case.status == "lab").order_by(Case.created_at.desc()).limit(20)).all()
    out = []
    for c in cases:
        m = s.exec(select(Media).where(Media.case_id == c.id)).first()
        if m:
            out.append({"case_id": c.id, "media_id": m.id, "filename": m.filename, "modality": m.modality,
                        "created_at": c.created_at.isoformat()})
    return out


@router.get("/lab/result/{case_id}/{media_id}")
def lab_result(case_id: str, media_id: str, s: Session = Depends(get_session)):
    m = s.get(Media, media_id)
    if not m or m.case_id != case_id:
        raise HTTPException(404)
    runs = s.exec(select(AnalysisRun).where(AnalysisRun.media_id == media_id)).all()
    claims = load_claims(s, case_id, "decomposition", media_id)
    arts = s.exec(select(Artifact).where(Artifact.media_id == media_id)).all()
    job = s.exec(select(Job).where(Job.case_id == case_id).order_by(Job.created_at.desc())).first()
    by_comp: dict[str, list] = {}
    for c in claims:
        by_comp.setdefault(c.produced_by.split("@")[0], []).append(c.model_dump(mode="json"))
    out = []
    for r in sorted(runs, key=lambda r: r.started_at):
        dur = (r.finished_at - r.started_at).total_seconds() if r.finished_at else None
        out.append({**r.model_dump(mode="json"), "duration_s": round(dur, 2) if dur is not None else None,
                    "claims": by_comp.get(r.component, []),
                    "artifacts": [a.model_dump(mode="json") for a in arts if a.analyzer == r.component]})
    return {"media": m.model_dump(mode="json", exclude={"fingerprints"}),
            "fingerprints": {k: (v if k != "video" else {"frames": len(v.get("frames", []))})
                             for k, v in (m.fingerprints or {}).items() if k != "audio"},
            "job": job.model_dump(mode="json") if job else None, "runs": out,
            "explanation": media_explanation(s, case_id, media_id) if runs else None,
            "thumb": next((a.sha256 for a in arts if a.kind == "thumb"), None),
            "checked_at": datetime.now(timezone.utc).isoformat()}


# --------------------------------------------------------------------------- benchmarks
@router.post("/lab/benchmark")
async def create_benchmark(files: list[UploadFile] = File(...), name: str = "Benchmark", visible: bool = False):
    from .. import benchmark
    payload = []
    for f in files:
        data = await f.read()
        if data:
            payload.append((f.filename or "file", data, None))
    if not payload:
        raise HTTPException(400, "no files")
    rec = benchmark.create(name, payload, visible_cases=visible)
    job = jobs.submit("benchmark", None, lambda p: benchmark.run(rec["id"], p), target=rec["id"])
    return {"benchmark": rec["id"], "job_id": job.id, "n": len(payload)}


@router.get("/lab/benchmarks")
def list_benchmarks():
    from .. import benchmark
    return benchmark.list_all()


@router.get("/lab/benchmark/{bid}")
def benchmark_results(bid: str, s: Session = Depends(get_session)):
    from .. import benchmark
    try:
        res = benchmark.results(bid)
    except FileNotFoundError as e:
        raise HTTPException(404) from e
    job = s.exec(select(Job).where(Job.target == bid).order_by(Job.created_at.desc())).first()
    res["job"] = job.model_dump(mode="json") if job else None
    return res


@router.get("/lab/calibration")
def calibration_info():
    from ..analyzers.verdict import calibration
    return calibration() or {"status": "not calibrated"}
