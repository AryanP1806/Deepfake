"""Forensic evidence package export (ZIP)."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from ..core.config import PROJECT_ROOT, host_info
from ..core.db import case_dir, load_claims, vault_for
from ..core.models import Case, Location, Media, TimelineNote
from ..core.vault import hash_bytes
from ..graph.build import build_graph, graph_json
from ..pipeline import load_genealogy
from ..reasoning.attribution import build_leads
from ..reasoning.gaps import analyse_gaps
from ..reasoning.timeline import build_timeline
from .bsa63_cert import certificate_docx
from .case_file import html_to_pdf, render_case_file
from .complaint import complaint_docx, get_or_create

FOLDER = {"original": "originals", "download": "discovered_media", "capture_screenshot": "captures/screenshots",
          "capture_html": "captures/html", "capture_mhtml": "captures/mhtml", "capture_headers": "captures/headers",
          "artifact": "analysis_artifacts"}


def export_package(s: Session, case_id: str, actor: str = "investigator") -> Path:
    case = s.get(Case, case_id)
    v = vault_for(case_id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = case_dir(case_id) / "exports"
    out_dir.mkdir(exist_ok=True)
    zpath = out_dir / f"evidence_package_{case_id}_{stamp}.zip"
    v.custody.append("export.start", actor=actor, details={"package": zpath.name})

    files: list[tuple[str, bytes]] = []
    records = v.all_records()
    for r in records:
        folder = FOLDER.get(r["kind"], "other")
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in r["filename"])[:80]
        files.append((f"{folder}/{r['sha256'][:12]}_{safe}", v.path(r["sha256"]).read_bytes()))
        files.append((f"{folder}/{r['sha256'][:12]}_{safe}.acquisition.json", json.dumps(r, indent=2).encode()))

    media = s.exec(select(Media).where(Media.case_id == case_id)).all()
    locs = s.exec(select(Location).where(Location.case_id == case_id)).all()
    manifest = {
        "package_created": stamp, "generator": host_info(), "case": case.model_dump(mode="json", exclude={"complainant"}),
        "media": [m.model_dump(mode="json", exclude={"fingerprints"}) for m in media],
        "locations": [l.model_dump(mode="json") for l in locs],
        "claims": [c.model_dump(mode="json") for c in load_claims(s, case_id)],
        "genealogy": load_genealogy(case_id),
        "timeline": build_timeline(s, case_id),
        "graph": graph_json(build_graph(s, case_id)),
        "leads": build_leads(s, case_id),
        "gaps": analyse_gaps(s, case_id),
        "user_log": [n.model_dump(mode="json") for n in s.exec(select(TimelineNote).where(TimelineNote.case_id == case_id)).all()],
        "status_legend": {"Verified": "direct/cryptographic observation", "Corroborated": "two or more independent sources",
                          "Inferred": "derived by reasoning", "Lead": "technical similarity only", "Unknown": "explicit gap"},
    }
    files.append(("manifest.json", json.dumps(manifest, indent=2, default=str).encode()))
    comp = get_or_create(s, case_id)
    files.append(("report/complaint_draft.json", json.dumps(comp.fields, indent=2, default=str).encode()))
    files.append(("report/complaint_draft.docx", complaint_docx(comp.fields, case.title)))
    html_doc = render_case_file(s, case_id)
    files.append(("report/case_file.html", html_doc.encode("utf-8")))
    pdf = html_to_pdf(html_doc)
    if pdf:
        files.append(("report/case_file.pdf", pdf))
    files.append(("report/BSA_s63_certificate_DRAFT.docx",
                  certificate_docx(case.model_dump(mode="json"), [r for r in records if r["kind"] != "artifact"])))
    files.append(("verify_package.py", (PROJECT_ROOT / "scripts" / "verify_package.py").read_bytes()))
    files.append(("README.txt", README.format(case=case.title, id=case.id, stamp=stamp).encode()))
    # custody log snapshot last (includes export.start)
    custody_bytes = (case_dir(case_id) / "custody_log.jsonl").read_bytes()
    files.append(("custody_log.jsonl", custody_bytes))

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["path", "sha256", "md5", "size"])
    for name, data in files:
        h = hash_bytes(data)
        w.writerow([name, h["sha256"], h["md5"], len(data)])
    files.append(("hashes.csv", buf.getvalue().encode()))

    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    zh = hash_bytes(zpath.read_bytes())
    v.custody.append("export.complete", actor=actor, item_sha256=zh["sha256"],
                     details={"package": zpath.name, "files": len(files), "md5": zh["md5"]})
    return zpath


README = """DFIR evidence package
=====================
Case: {case} ({id})
Created (UTC): {stamp}

Contents
  originals/            files supplied by the complainant (unaltered bytes)
  discovered_media/     media downloaded from captured public pages
  captures/             page screenshots, rendered HTML, MHTML snapshots and HTTP headers
  analysis_artifacts/   forensic heatmaps, spectrograms, frame strips, OCR/transcripts
  report/               case file (PDF/HTML), complaint draft (DOCX/JSON), BSA s.63 certificate DRAFT
  manifest.json         every claim with status, genealogy, timeline, graph, leads and gaps
  custody_log.jsonl     hash-chained chain-of-custody log
  hashes.csv            SHA-256 / MD5 of every file in this package

Verify:  python verify_package.py

Every finding is labelled Verified / Corroborated / Inferred / Lead / Unknown.
Detector outputs are evidence, not verdicts. Accounts are listed as observed posting the media.
This is not an allegation that they created it.
"""
