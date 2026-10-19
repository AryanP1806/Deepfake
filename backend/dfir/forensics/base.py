"""Contract shared by every forensic module.

A module answers ONE forensic question about ONE media file and always returns the same shape:

    findings     what was measured / concluded, each tagged with how far it can be trusted
    confidence   how strongly the module's own evidence supports its headline, CAPPED by how well the method has been
                 validated (a method that has only been tested on synthetic data can never report "high")
    limitations  what the method cannot tell you
    versions     exact tool / library / model versions that produced the numbers
    evidence     artefacts (heat maps, plots), claims or raw values the findings rest on
    unknowns     what could not be assessed and why

Finding kinds keep measurement and interpretation apart:

    observation  a direct, reproducible measurement ("2.4 % of samples sit at full scale"). No interpretation.
    supported    an interpretation whose error rates were MEASURED against ground truth (see validation_results.json)
    hypothesis   an interpretation that has not been validated against ground truth. A lead, never a conclusion.
    unknown      could not be assessed. Missing evidence is always Unknown, never "clean".
"""
from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

API_VERSION = 1  # bump when the module contract changes incompatibly; plugins declare the version they were written for

FindingKind = Literal["observation", "supported", "hypothesis", "unknown"]
Tier = Literal["real", "synthetic", "none"]
VALIDATION_FILE = Path(__file__).with_name("validation_results.json")

# the most a module may claim, by how its method was validated
CONFIDENCE_CAP: dict[str, float] = {"real": 0.95, "synthetic": 0.70, "none": 0.35}


def confidence_label(v: float | None) -> str:
    if v is None:
        return "unknown"
    return "very low" if v < 0.2 else "low" if v < 0.4 else "moderate" if v < 0.6 else "high" if v < 0.85 else "very high"


class Requirement(BaseModel):
    kind: Literal["python", "model", "binary"]
    name: str
    how_to_enable: str = ""
    optional: bool = False  # optional requirements improve the result but are not needed to run
    ok: bool | None = None
    version: str | None = None


class Finding(BaseModel):
    id: str  # stable slug, e.g. "cfa.pattern"
    title: str
    statement: str
    kind: FindingKind
    confidence: float | None = None  # capped by validation tier; None for observations/unknowns
    value: Any = None  # the headline number, if any
    unit: str = ""
    why: str = ""  # plain-language meaning
    caution: str = ""  # what could make this misleading
    evidence: list[str] = Field(default_factory=list)  # ids of ModuleResult.evidence entries
    data: dict[str, Any] = Field(default_factory=dict)
    emit_claim: bool = True  # False when an existing analyzer already recorded the same fact as a claim

    @property
    def confidence_label(self) -> str:
        return confidence_label(self.confidence)


class Evidence(BaseModel):
    id: str
    kind: Literal["image", "plot", "table", "claim", "value", "file"]
    label: str
    sha256: str | None = None  # vault object, when the evidence was stored
    claim_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Validation(BaseModel):
    tier: Tier = "none"
    summary: str = "Not validated against ground-truth data."
    metrics: dict[str, Any] = Field(default_factory=dict)
    dataset: str = ""


class ModuleResult(BaseModel):
    module: str
    title: str
    version: str
    api_version: int = API_VERSION
    status: Literal["ok", "partial", "not_applicable", "unavailable", "error"] = "ok"
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    confidence: float | None = None
    confidence_label: str = "unknown"
    confidence_basis: str = ""
    limitations: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    validation: Validation = Field(default_factory=Validation)
    requirements: list[Requirement] = Field(default_factory=list)
    media: dict[str, Any] = Field(default_factory=dict)
    run_at: str = ""
    runtime_ms: int = 0
    error: str = ""

    def finding(self, fid: str) -> Finding | None:
        return next((f for f in self.findings if f.id == fid), None)


# ------------------------------------------------------------------------------------------------ validation lookup
_val_cache: dict[str, Any] = {"mtime": None, "data": {}}


def validation_data() -> dict:
    try:
        mt = VALIDATION_FILE.stat().st_mtime
    except OSError:
        return {}
    if _val_cache["mtime"] != mt:
        try:
            _val_cache["data"] = json.loads(VALIDATION_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _val_cache["data"] = {}
        _val_cache["mtime"] = mt
    return _val_cache["data"]


def validation_for(module_id: str) -> Validation:
    """What was measured for this module (and on what kind of data). Missing results mean 'not validated'."""
    rec = (validation_data().get("modules") or {}).get(module_id)
    if not rec:
        return Validation()
    return Validation(tier=rec.get("tier", "none"), summary=rec.get("summary", ""), metrics=rec.get("metrics", {}),
                      dataset=rec.get("dataset", ""))


def tool_versions(*names: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for n in names:
        try:
            if n == "ffmpeg":
                from ..analyzers.media_io import ffmpeg_version
                out[n] = ffmpeg_version()
                continue
            mod = importlib.import_module({"cv2": "cv2", "PIL": "PIL", "pywt": "pywt", "c2pa": "c2pa"}.get(n, n))
            v = getattr(mod, "__version__", None)
            if n == "c2pa" and hasattr(mod, "sdk_version"):
                v = mod.sdk_version()
            out[n] = str(v or "unknown")
        except Exception:  # noqa: BLE001
            out[n] = "not installed"
    return out


# ------------------------------------------------------------------------------------------------ run context
@dataclass
class FCtx:
    """What a module sees. Works on a bare file (tests, benchmarks) or inside an analysis run (vault, claims, shared cache)."""

    path: Path
    modality: str  # image | video | audio
    media: dict = field(default_factory=dict)  # {id, sha256, filename, width, height, ...}
    case_id: str | None = None
    shared: dict[str, Any] = field(default_factory=dict)
    claims_fn: Callable[[], list] | None = None  # existing claims for this media (metadata, c2pa, ...)
    artifact_fn: Callable[[str, str, Any], str | None] | None = None  # (kind, label, image|bytes) -> vault sha256
    candidate_devices: list[dict] = field(default_factory=list)  # fingerprints available for the PRNU comparison
    options: dict[str, Any] = field(default_factory=dict)
    _stored: list[tuple[str, str]] = field(default_factory=list)

    def claims(self) -> list:
        return self.claims_fn() if self.claims_fn else []

    def store(self, kind: str, label: str, content: Image.Image | bytes) -> str | None:
        """Persist a derived image/plot in the evidence vault (no-op outside a case). Returns its SHA-256."""
        if self.artifact_fn is None:
            return None
        sha = self.artifact_fn(kind, label, content)
        if sha:
            self._stored.append((kind, sha))
        return sha

    # -- cached decoders (modules share them so a file is decoded once per run)
    def native_rgb(self, max_side: int | None = 1536) -> np.ndarray:
        """Pixels at NATIVE resolution (never resampled): resampling would destroy the very traces modules look for.
        Larger images are centre-cropped to ``max_side`` on an 16-pixel grid so JPEG-block and CFA phase are preserved."""
        key = f"native_rgb:{max_side}"
        if key not in self.shared:
            from ..analyzers.media_io import load_image
            im = load_image(self.path, apply_exif_orientation=False).convert("RGB")
            a = np.asarray(im)
            if max_side and max(a.shape[:2]) > max_side:
                h, w = a.shape[:2]
                ch, cw = min(h, max_side), min(w, max_side)
                y, x = ((h - ch) // 2) // 16 * 16, ((w - cw) // 2) // 16 * 16
                a = a[y:y + ch, x:x + cw]
            self.shared[key] = a
        return self.shared[key]

    def run_timer(self) -> float:
        return time.perf_counter()


class ForensicModule:
    """Base class for a forensic module (built-in or plugin)."""

    id: str = "module"
    title: str = ""
    question: str = ""  # the one plain-language question this module answers
    version: str = "0.1"
    modalities: tuple[str, ...] = ("image",)
    requirements: list[Requirement] = []  # python packages / model files / binaries
    model_specs: list = []  # ModelSpec objects for optional weights; registered so setup_models.py can fetch them
    limitations: list[str] = []
    library_versions: tuple[str, ...] = ("numpy", "scipy")
    order: int = 50

    # -- environment
    def check_requirements(self) -> list[Requirement]:
        """Resolve every requirement. A missing NON-optional requirement makes the module unavailable."""
        out = []
        for r in self.requirements:
            r = r.model_copy()
            r.ok, r.version = _probe(r)
            out.append(r)
        return out

    def unavailable_reason(self) -> str | None:
        miss = [r for r in self.check_requirements() if not r.optional and not r.ok]
        if not miss:
            return None
        return "; ".join(f"{r.kind} '{r.name}' is missing" + (f" ({r.how_to_enable})" if r.how_to_enable else "") for r in miss)

    def applies(self, modality: str) -> bool:
        return modality in self.modalities

    # -- execution
    def analyse(self, fx: FCtx) -> ModuleResult:
        raise NotImplementedError

    def execute(self, fx: FCtx) -> ModuleResult:
        """Run with uniform error handling, timing, versioning and validation lookup. Never raises."""
        t0 = time.perf_counter()
        base = dict(module=self.id, title=self.title, version=self.version,
                    media={k: fx.media.get(k) for k in ("id", "sha256", "filename") if fx.media.get(k)},
                    versions=tool_versions(*self.library_versions), validation=validation_for(self.id),
                    requirements=self.check_requirements(), run_at=datetime.now(timezone.utc).isoformat())
        if not self.applies(fx.modality):
            return ModuleResult(**base, status="not_applicable",
                                summary=f"{self.title} applies to {', '.join(self.modalities)} files, not {fx.modality}.",
                                limitations=list(self.limitations))
        why = self.unavailable_reason()
        if why:
            return ModuleResult(**base, status="unavailable", summary=f"Not run: {why}", limitations=list(self.limitations),
                                unknowns=[f"{self.title} was not assessed ({why})."])
        try:
            res = self.analyse(fx)
        except Exception as e:  # noqa: BLE001
            return ModuleResult(**base, status="error", summary=f"The module failed: {type(e).__name__}",
                                error=f"{type(e).__name__}: {e}"[:500], limitations=list(self.limitations),
                                unknowns=[f"{self.title} could not be assessed because it failed to run."])
        for k, v in base.items():
            if k in ("requirements",) or not getattr(res, k, None):
                setattr(res, k, v)
        res.versions = {**base["versions"], **res.versions}
        res.limitations = list(dict.fromkeys([*res.limitations, *self.limitations]))
        res.validation = base["validation"]
        self.finalise(res)
        res.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return res

    def finalise(self, res: ModuleResult) -> None:
        """Cap every confidence by how well the method has been validated, and derive the module-level confidence."""
        cap = CONFIDENCE_CAP[res.validation.tier]
        scored = [f for f in res.findings if f.kind in ("supported", "hypothesis") and f.confidence is not None]
        for f in res.findings:
            if f.confidence is not None:
                # a hypothesis is by definition not validated: never above the 'none' cap, whatever the module says
                f.confidence = round(min(f.confidence, cap if f.kind == "supported" else CONFIDENCE_CAP["none"]), 3)
        if scored:
            res.confidence = max(f.confidence for f in scored if f.confidence is not None)
            res.confidence_label = confidence_label(res.confidence)
            res.confidence_basis = (f"Capped at {cap:.2f} because the method is validated on {res.validation.tier} data only."
                                    if res.validation.tier != "real" else "Validated on real labelled data.")
        else:
            res.confidence, res.confidence_label = None, "unknown"
            res.confidence_basis = "No interpretive finding was made, so there is nothing to attach a confidence to."
        res.unknowns = list(dict.fromkeys([*res.unknowns, *[f.statement for f in res.findings if f.kind == "unknown"]]))


def _probe(r: Requirement) -> tuple[bool, str | None]:
    try:
        if r.kind == "python":
            mod = importlib.import_module(r.name)
            return True, str(getattr(mod, "__version__", "") or "") or None
        if r.kind == "binary":
            import shutil
            return (shutil.which(r.name) is not None), None
        if r.kind == "model":
            from ..core import model_store
            st = model_store.status(r.name)
            return bool(st.get("installed")), None
    except Exception:  # noqa: BLE001
        return False, None
    return False, None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (honest bounds even when k is 0 or n is small)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, c - h), min(1.0, c + h)
