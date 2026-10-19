"""Runs the pluggable forensic modules (sensor noise, camera processing, editing, video, audio, provenance) as one analyzer.

The modules themselves live in ``dfir/forensics`` and can be extended with plugins; this wrapper only connects them to the
analysis run, the evidence vault and the custody ledger (see ``dfir/forensics/runner.py``).
"""
from __future__ import annotations

from .base import Analyzer, Ctx, register


@register
class ForensicSuite(Analyzer):
    order = 90  # after metadata, C2PA, watermark, pixel forensics and audio, so modules can reuse their output
    name = "forensic_suite"
    version = "1.0"
    modalities = ("image", "video", "audio")
    title = "Forensic modules (sensor noise, camera processing, editing, video, audio, provenance)"

    def run(self, ctx: Ctx):
        from ..core.db import session
        from ..forensics.runner import run_modules
        with session() as s:  # own session: modules read claims that earlier analyzers already saved
            run_modules(s, ctx)
