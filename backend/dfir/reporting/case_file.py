"""Investigator-ready case file (HTML → PDF via headless Chromium)."""
from __future__ import annotations

import base64
import html
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..core.config import host_info
from ..core.db import load_claims, vault_for
from ..core.models import Artifact, Case, Location, Media
from ..pipeline import load_genealogy, media_explanation
from ..reasoning.attribution import build_leads
from ..reasoning.gaps import analyse_gaps
from ..reasoning.narrative import build_narrative
from ..reasoning.next_action import next_action
from ..reasoning.timeline import build_timeline

STATUS_COLORS = {"Verified": "#15803d", "Corroborated": "#1d4ed8", "Inferred": "#b45309", "Lead": "#7e22ce",
                 "Unknown": "#6b7280"}

CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;font-size:10.5pt;color:#111;margin:28px}
h1{font-size:20pt;margin:0}h2{font-size:13.5pt;border-bottom:2px solid #111;padding-bottom:3px;margin-top:26px}
h3{font-size:11.5pt;margin:14px 0 4px}table{border-collapse:collapse;width:100%;margin:6px 0}
td,th{border:1px solid #ccc;padding:4px 6px;vertical-align:top;font-size:9pt;text-align:left}th{background:#f3f4f6}
.st{display:inline-block;padding:1px 6px;border-radius:9px;color:#fff;font-size:8pt;font-weight:600}
.muted{color:#555}.mono{font-family:Consolas,monospace;font-size:8pt;word-break:break-all}
.banner{background:#fef3c7;border:1px solid #f59e0b;padding:8px;margin:10px 0}
.imgs img{max-height:180px;margin:4px;border:1px solid #ddd}.blur img{filter:blur(14px)}
.pb{page-break-before:always}
"""


def _st(s: str) -> str:
    return f'<span class="st" style="background:{STATUS_COLORS.get(s, "#555")}">{html.escape(s)}</span>'


def _img(v, sha: str, blur: bool = False) -> str:
    try:
        b = v.path(sha).read_bytes()
    except FileNotFoundError:
        return ""
    return f'<img src="data:image/png;base64,{base64.b64encode(b).decode()}"/>'


def render_case_file(s: Session, case_id: str) -> str:
    case = s.get(Case, case_id)
    v = vault_for(case_id)
    media = s.exec(select(Media).where(Media.case_id == case_id)).all()
    locs = s.exec(select(Location).where(Location.case_id == case_id, Location.dismissed == False)).all()  # noqa: E712
    blur = bool(case.safety.get("intimate") or case.safety.get("minors_possible"))
    nar = build_narrative(s, case_id)
    na = next_action(s, case_id)
    out = [f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>",
           f"<h1>Deepfake Incident Case File</h1><div class='muted'>{html.escape(case.title)} · case {case.id} · generated "
           f"{datetime.now(timezone.utc).isoformat()} by {html.escape(host_info()['app'])}</div>",
           "<div class='banner'><b>Reading this file:</b> every statement has a status — " +
           " ".join(_st(k) for k in STATUS_COLORS) +
           ". Detector outputs are evidence, not verdicts. \"Earliest discovered\" is not \"original\". Accounts are "
           "listed because they were observed posting, not as findings of who created the media.</div>"]
    if case.is_demo:
        out.append("<div class='banner'><b>DEMO CASE</b> — synthetic data generated for demonstration.</div>")
    out.append("<h2>1. Summary</h2>")
    for sec in nar["sections"]:
        out.append(f"<h3>{html.escape(sec['title'])}</h3><ul>")
        for it in sec["items"]:
            out.append(f"<li>{html.escape(it['text'])}</li>")
        out.append("</ul>")
    out.append(f"<h3>Recommended next action</h3><p><b>{html.escape(na['primary']['title'])}</b> — "
               f"{html.escape(na['primary']['why'])}</p>")

    out.append("<h2 class='pb'>2. Forensic decomposition</h2>")
    arts = s.exec(select(Artifact).where(Artifact.case_id == case_id)).all()
    for m in [m for m in media if m.role == "primary"] + [m for m in media if m.role != "primary"]:
        ex = media_explanation(s, case_id, m.id)
        out.append(f"<h3>{html.escape(m.label)} <span class='muted'>({m.modality}, {m.mime}, {m.size:,} bytes)</span></h3>")
        out.append(f"<div class='mono'>SHA-256 {m.sha256} · MD5 {m.md5}</div>")
        out.append(f"<p><b>{html.escape(ex['headline'])}</b> (level: {ex['level']})</p>")
        imgs = [a for a in arts if a.media_id == m.id and a.mime == "image/png" and a.kind in ("thumb", "ela", "noise", "faces", "frames", "spectrogram")]
        if imgs and m.role == "primary":
            out.append(f"<div class='imgs{' blur' if blur else ''}'>" + "".join(_img(v, a.sha256) for a in imgs[:5]) + "</div>")
        if m.role == "primary":
            out.append("<table><tr><th>Status</th><th>Finding</th><th>Limitations</th><th>Produced by</th></tr>")
            for c in load_claims(s, case_id, "decomposition", m.id):
                out.append(f"<tr><td>{_st(c.status.value)}</td><td>{html.escape(c.statement)}</td>"
                           f"<td class='muted'>{html.escape(c.limitations)}</td><td class='mono'>{html.escape(c.produced_by)}</td></tr>")
            out.append("</table>")
            if ex["not_checked"]:
                out.append("<p class='muted'><b>Not checked:</b> " + "; ".join(
                    html.escape(f"{n['component']}: {n['reason']}") for n in ex["not_checked"]) + "</p>")

    out.append("<h2 class='pb'>3. Media genealogy</h2>")
    gen = load_genealogy(case_id)
    lab = {m.id: m.label for m in media}
    out.append("<table><tr><th>Ancestor (inferred)</th><th>Descendant</th><th>Transformations</th><th>Match</th><th>Direction</th></tr>")
    for e in gen.get("edges", []):
        out.append(f"<tr><td>{html.escape(lab.get(e['parent'], e['parent']))}</td><td>{html.escape(lab.get(e['child'], e['child']))}</td>"
                   f"<td>{html.escape(', '.join(t['op'] for t in e['transforms']) or '—')}</td>"
                   f"<td>{_st('Verified' if e['verified_match'] else 'Lead')} {e['match_tier']}</td><td>{_st(e['direction_confidence'])}</td></tr>")
    out.append("</table>")
    for f in gen.get("families", []):
        if len(f["members"]) > 1:
            out.append(f"<p>Family of {len(f['members'])}: inferred closest-to-source copy <b>{html.escape(lab.get(f['root'], ''))}</b>; "
                       f"earliest observed <b>{html.escape(lab.get(f.get('earliest_observed') or '', 'unknown'))}</b>. "
                       "Neither is proven to be the original.</p>")

    out.append("<h2>4. Propagation — locations</h2><table><tr><th>URL</th><th>Platform</th><th>Account</th><th>Published</th>"
               "<th>Captured</th><th>State</th><th>Screenshot SHA-256</th></tr>")
    for l in sorted(locs, key=lambda l: (l.published_at or l.added_at)):
        out.append(f"<tr><td class='mono'>{html.escape(l.url)}</td><td>{html.escape(l.platform)}</td>"
                   f"<td>{html.escape(l.account_handle)}</td><td>{l.published_at.isoformat() if l.published_at else '—'}</td>"
                   f"<td>{l.first_captured_at.isoformat() if l.first_captured_at else '—'}</td><td>{l.state}</td>"
                   f"<td class='mono'>{l.screenshot_sha[:16]}</td></tr>")
    out.append("</table>")

    out.append("<h2 class='pb'>5. Timeline</h2><table><tr><th>Time</th><th>Event</th><th>Status</th><th>Time source</th></tr>")
    for e in build_timeline(s, case_id)["events"]:
        out.append(f"<tr><td>{html.escape(e['at'] or ('before ' + (e['end'] or '?')))}</td><td>{html.escape(e['title'])}</td>"
                   f"<td>{_st(e['status'])}</td><td class='muted'>{html.escape(e['time_source'])}</td></tr>")
    out.append("</table>")

    out.append("<h2>6. Attribution leads</h2>")
    leads = build_leads(s, case_id)
    out.append(f"<p class='muted'>{html.escape(leads['principle'])}</p>")
    for l in leads["leads"][:8]:
        out.append(f"<h3>{html.escape(l['subject'])} — evidence strength: {l['evidence_strength']}</h3><ul>")
        for d in l["discovered"]:
            out.append(f"<li>{_st(d['status'])} {html.escape(d['text'])}</li>")
        out.append(f"</ul><p><b>Suggests:</b> {html.escape(l['suggests'])}</p>"
                   f"<p><b>Unknown:</b> {html.escape('; '.join(l['unknown']))}</p>"
                   f"<p><b>Required for attribution:</b> {html.escape('; '.join(l['required']))}</p>")

    out.append("<h2>7. Evidence gaps (prioritised)</h2><table><tr><th>#</th><th>Open question</th><th>Next evidence</th><th>Who</th></tr>")
    for i, g in enumerate(analyse_gaps(s, case_id), 1):
        out.append(f"<tr><td>{i}</td><td>{html.escape(g['question'])}</td><td>{html.escape(g['next_evidence'])}</td><td>{html.escape(g['who'])}</td></tr>")
    out.append("</table>")

    out.append("<h2 class='pb'>8. Evidence register</h2><table><tr><th>File</th><th>Kind</th><th>SHA-256</th><th>Size</th><th>Acquired (UTC)</th></tr>")
    for r in v.all_records():
        out.append(f"<tr><td>{html.escape(r['filename'])}</td><td>{r['kind']}</td><td class='mono'>{r['sha256']}</td>"
                   f"<td>{r['size']:,}</td><td>{(r.get('stored_at') or {}).get('utc', '')}</td></tr>")
    out.append("</table><p class='muted'>The chain-of-custody log (custody_log.jsonl) is hash-chained. "
               "Run verify_package.py in the package folder to check every file hash and the integrity of the log.</p>")
    out.append("</body></html>")
    return "".join(out)


def html_to_pdf(html_doc: str) -> bytes | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.set_content(html_doc, wait_until="load")
            pdf = pg.pdf(format="A4", print_background=True,
                         margin={"top": "12mm", "bottom": "14mm", "left": "10mm", "right": "10mm"},
                         display_header_footer=True, header_template="<span></span>",
                         footer_template="<div style='font-size:7pt;width:100%;text-align:center;color:#666'>"
                                         "Page <span class='pageNumber'></span> / <span class='totalPages'></span></div>")
            b.close()
            return pdf
    except Exception:  # noqa: BLE001
        return None
