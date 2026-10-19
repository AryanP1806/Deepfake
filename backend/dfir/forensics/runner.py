"""Runs forensic modules inside an analysis run and records their output in the case.

Everything a module produces is written to the evidence vault and the chain-of-custody ledger:

  * the full ModuleResult as a JSON artifact (hashed, read-only) - the record of what was found, how, and with which versions;
  * heat maps / plots the module stored (hashed artifacts);
  * a claim for each interpretive finding (supported / hypothesis), so it appears with the rest of the evidence;
  * one custody entry per module run (tool, version, status, result hash).

Module findings are claims with NEUTRAL polarity: they sit beside the verdict and do not change it until a method has been
validated on real data.
"""
from __future__ import annotations

import json
from typing import Callable

from sqlmodel import Session, select

from ..analyzers.base import Ctx
from ..core import prefs
from ..core.claims import Polarity, Status, Strength
from ..core.db import load_claims, save_claims
from ..core.models import Artifact, ClaimRow
from . import devices, registry
from .base import FCtx, ModuleResult

KIND = "forensic_module"


def _noop(_f: float, _m: str) -> None:
    pass


def enabled_modules(modality: str, ids: set[str] | None = None):
    """Modules that apply to this kind of media. An explicit id set overrides the user's disabled list."""
    disabled = set(prefs.load()["forensics"]["disabled_modules"])
    return [m for m in registry.all_modules() if m.applies(modality) and ((m.id in ids) if ids is not None else (m.id not in disabled))]


def _candidates(ctx: Ctx) -> list[dict]:
    out = []
    for d in devices.list_devices(ctx.case_id):
        c = {k: d[k] for k in ("id", "name", "height", "width", "n_references", "coherent") if k in d}
        if (d["height"], d["width"]) == (ctx.media.height, ctx.media.width):
            try:
                c["fingerprint"] = devices.load_fingerprint(ctx.case_id, d)
            except Exception:  # noqa: BLE001
                continue
        out.append(c)
    return out


def _status_of(kind: str, conf: float | None) -> tuple[Status, Strength]:
    if kind == "supported":
        return Status.INFERRED, (Strength.MODERATE if (conf or 0) >= 0.5 else Strength.WEAK)
    return Status.LEAD, Strength.WEAK


def run_modules(s: Session, ctx: Ctx, ids: set[str] | None = None, progress: Callable[[float, str], None] = _noop) -> list[ModuleResult]:
    """Execute the selected modules for ctx.media and persist their results. Never raises."""
    mods = enabled_modules(ctx.media.modality, ids)
    results: list[ModuleResult] = []
    cands = _candidates(ctx) if ctx.media.modality == "image" and any(m.id == "prnu" for m in mods) else []

    def claims_fn() -> list:
        seen = {c.id for c in ctx.claims}
        try:
            db = load_claims(s, ctx.case_id, "decomposition", ctx.media.id)
        except Exception:  # noqa: BLE001
            db = []
        return list(ctx.claims) + [c for c in db if c.id not in seen]

    for i, m in enumerate(mods):
        progress(i / max(1, len(mods)), m.title)

        def art_fn(kind, label, content, m=m):
            return ctx.artifact(f"fm_{kind}", f"{m.title}: {label}", content).sha256

        fx = FCtx(path=ctx.path, modality=ctx.media.modality, case_id=ctx.case_id, shared=ctx.shared,
                  media={"id": ctx.media.id, "sha256": ctx.media.sha256, "filename": ctx.media.filename,
                         "width": ctx.media.width, "height": ctx.media.height},
                  claims_fn=claims_fn, artifact_fn=art_fn, candidate_devices=cands)
        res = m.execute(fx)
        results.append(res)
        payload = json.dumps(res.model_dump(mode="json"), indent=2, default=str).encode("utf-8")
        art = ctx.artifact(KIND, res.title, payload, mime="application/json",
                           data={"module": m.id, "version": m.version, "status": res.status, "confidence": res.confidence,
                                 "n_findings": len(res.findings), "validation_tier": res.validation.tier})
        for f in res.findings:
            if f.kind not in ("supported", "hypothesis") or not f.emit_claim:
                continue
            st, strength = _status_of(f.kind, f.confidence)
            c = ctx.claim("forensic_module", f"{res.title}: {f.statement}", st, strength=strength, polarity=Polarity.NEUTRAL, why=f.why,
                          limitations=f.caution, data={"module": m.id, "finding": f.id, "kind": f.kind, "confidence": f.confidence,
                                                       "validation_tier": res.validation.tier},
                          evidence=[art.sha256], sources=[f"forensic_module:{m.id}@{m.version}"])
            c.what_would_confirm = "Independent confirmation with a validated method, reference material, or the original file."
        ctx.vault.custody.append(f"forensics.{m.id}.{res.status}", tool=f"{m.id}@{m.version}", item_sha256=ctx.media.sha256,
                                 details={"status": res.status, "confidence": res.confidence, "findings": len(res.findings),
                                          "result_sha256": art.sha256, "versions": res.versions, "validation_tier": res.validation.tier,
                                          "runtime_ms": res.runtime_ms, **({"error": res.error} if res.error else {})})
    return results


# ------------------------------------------------------------------------------------------------ reading results back
def latest_results(s: Session, case_id: str, media_id: str) -> list[dict]:
    from ..core.db import vault_for
    v = vault_for(case_id)
    rows = s.exec(select(Artifact).where(Artifact.case_id == case_id, Artifact.media_id == media_id, Artifact.kind == KIND)).all()
    out = []
    for a in rows:
        try:
            out.append({**json.loads(v.path(a.sha256).read_text(encoding="utf-8")), "result_sha256": a.sha256})
        except (OSError, json.JSONDecodeError):
            continue
    order = {m.id: m.order for m in registry.all_modules()}
    return sorted(out, key=lambda r: order.get(r["module"], 99))


def rerun(case_id: str, media_id: str, ids: set[str] | None, progress: Callable[[float, str], None] = _noop) -> list[dict]:
    """Re-run selected modules on an already-analysed file, replacing only those modules' previous output."""
    from ..core.db import session, vault_for
    from ..core.models import Case, Media
    with session() as s:
        case, media = s.get(Case, case_id), s.get(Media, media_id)
        v = vault_for(case_id)
        ctx = Ctx(case_id=case_id, media=media, path=v.path(media.sha256), vault=v, safety=case.safety or {})
        mods = {m.id for m in enabled_modules(media.modality, ids)}
        titles = {m.title for m in registry.all_modules() if m.id in mods}
        for a in s.exec(select(Artifact).where(Artifact.media_id == media_id, Artifact.analyzer == "forensic_suite")).all():
            if (a.kind == KIND and (a.data or {}).get("module") in mods) or (a.kind.startswith("fm_") and any(a.label.startswith(t) for t in titles)):
                s.delete(a)
        for row in s.exec(select(ClaimRow).where(ClaimRow.media_id == media_id, ClaimRow.stage == "decomposition")).all():
            if row.body.get("category") == "forensic_module" and (row.body.get("data") or {}).get("module") in mods:
                s.delete(row)
        s.commit()

        class _Suite:  # claims and artifacts made here are attributed to the suite, like in a full run
            name, version = "forensic_suite", "1.0"

        ctx.current = _Suite()  # type: ignore[assignment]
        run_modules(s, ctx, mods, progress)
        save_claims(s, ctx.claims)
        for art in ctx.artifacts:
            s.add(art)
        s.commit()
        return latest_results(s, case_id, media_id)
