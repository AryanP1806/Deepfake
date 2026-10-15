"""Analyzer plugin contract and registry."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image
from sqlmodel import Session

from ..core.claims import Claim, NotRun, Polarity, Status, Strength
from ..core.models import AnalysisRun, Artifact, Media
from ..core.vault import Vault
from .media_io import png_bytes


@dataclass
class Ctx:
    case_id: str
    media: Media
    path: Path
    vault: Vault
    safety: dict
    stage: str = "decomposition"
    claims: list[Claim] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    shared: dict[str, Any] = field(default_factory=dict)  # cross-analyzer cache (frames, audio, ...)
    current: "Analyzer | None" = None

    def claim(self, category: str, statement: str, status: Status, *, strength: Strength = Strength.NONE,
              polarity: Polarity = Polarity.NEUTRAL, why: str = "", limitations: str = "",
              confirm: str = "", data: dict | None = None, evidence: list[str] | None = None,
              sources: list[str] | None = None, model_sha: str | None = None) -> Claim:
        a = self.current
        produced_by = f"{a.name}@{a.version}" if a else "system"
        if model_sha:
            produced_by += f"#{model_sha[:12]}"
        c = Claim(case_id=self.case_id, media_id=self.media.id, stage=self.stage, category=category,
                  statement=statement, status=status, strength=strength, polarity=polarity, why=why,
                  limitations=limitations, what_would_confirm=confirm, data=data or {},
                  evidence_refs=[self.media.sha256] + (evidence or []), produced_by=produced_by,
                  sources=sources or [produced_by])
        self.claims.append(c)
        return c

    def artifact(self, kind: str, label: str, content: bytes | Image.Image, *, mime: str = "image/png",
                 data: dict | None = None) -> Artifact:
        payload = png_bytes(content) if isinstance(content, Image.Image) else content
        a = self.current
        rec = self.vault.store(payload, kind="artifact", filename=f"{self.media.id}_{kind}.{mime.split('/')[-1]}",
                               mime=mime, tool=f"{a.name}@{a.version}" if a else None,
                               acquisition={"derived_from": self.media.sha256, "analysis": kind})
        art = Artifact(case_id=self.case_id, media_id=self.media.id, sha256=rec["sha256"], kind=kind,
                       label=label, analyzer=a.name if a else "", mime=mime, data=data or {})
        self.artifacts.append(art)
        return art


class Analyzer:
    name: str = "analyzer"
    version: str = "0.1"
    modalities: tuple[str, ...] = ("image", "video", "audio")
    title: str = ""
    order: int = 50  # execution order (metadata first, OCR/ASR last)

    def available(self) -> NotRun | None:
        """Return NotRun if the analyzer can't run in this environment, else None."""
        return None

    def applies(self, media: Media) -> bool:
        return media.modality in self.modalities

    def run(self, ctx: Ctx) -> NotRun | None:
        raise NotImplementedError


_REGISTRY: list[Analyzer] = []
log = logging.getLogger("dfir.analyzers")


def register(cls: type[Analyzer]) -> type[Analyzer]:
    _REGISTRY.append(cls())
    return cls


def registry() -> list[Analyzer]:
    from . import (audio, c2pa_check, content_region, detectors_api, detectors_local, faces,  # noqa: F401
                   image_forensics, metadata, ocr, provenance_signals, asr, research_detectors, video)
    return sorted(_REGISTRY, key=lambda a: a.order)


def run_analyzers(s: Session, ctx: Ctx, progress: Callable[[float, str], None] | None = None,
                  only: set[str] | None = None) -> list[AnalysisRun]:
    from ..core.db import save_claims
    runs = []
    saved_c = len(ctx.claims)
    saved_a = len(ctx.artifacts)
    analyzers = [a for a in registry() if a.applies(ctx.media) and (only is None or a.name in only)]
    for i, a in enumerate(analyzers):
        if progress:
            progress(i / max(1, len(analyzers)), f"{a.title or a.name}")
        run = AnalysisRun(case_id=ctx.case_id, media_id=ctx.media.id, component=a.name, version=a.version,
                          outcome="ok", started_at=datetime.now(timezone.utc))
        nr = a.available()
        if nr is None:
            ctx.current = a
            try:
                nr = a.run(ctx)
            except Exception as e:  # noqa: BLE001
                run.outcome = "error"
                run.reason = f"{type(e).__name__}: {e}"[:500]
            finally:
                ctx.current = None
        if nr is not None:
            run.outcome = "not_run"
            run.reason = nr.reason
            run.how_to_enable = nr.how_to_enable
        run.finished_at = datetime.now(timezone.utc)
        log.info("%s on %s: %s in %.2fs%s", a.name, ctx.media.id, run.outcome,
                 (run.finished_at - run.started_at).total_seconds(), f" ({run.reason[:120]})" if run.reason else "")
        s.add(run)
        # save this analyzer's findings right away so the UI can show results while the run continues
        save_claims(s, ctx.claims[saved_c:])
        for art in ctx.artifacts[saved_a:]:
            s.add(art)
        saved_c, saved_a = len(ctx.claims), len(ctx.artifacts)
        s.commit()  # release SQLite's write lock between (possibly slow) analyzers
        ctx.vault.custody.append(f"analysis.{run.outcome}", tool=f"{a.name}@{a.version}",
                                 item_sha256=ctx.media.sha256, details={"reason": run.reason} if run.reason else {})
        runs.append(run)
    return runs
