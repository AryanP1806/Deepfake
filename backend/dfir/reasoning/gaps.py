"""Evidence-gap analysis: what the case still cannot prove, and the highest-value next evidence."""
from __future__ import annotations

from sqlmodel import Session, select

from ..core.models import AnalysisRun, Case, Complaint, Location, Media, MonitorConfig
from ..pipeline import load_genealogy, media_explanation

EFFORT = {"low": 1, "medium": 2, "high": 3}


def _gap(id, question, why, next_evidence, who, effort, value, urgency=1.0, action=None):
    return {"id": id, "question": question, "why": why, "next_evidence": next_evidence, "who": who, "effort": effort,
            "value": value, "urgency": urgency, "score": round(value * urgency / EFFORT[effort], 2), "action": action}


def analyse_gaps(s: Session, case_id: str) -> list[dict]:
    case = s.get(Case, case_id)
    media = s.exec(select(Media).where(Media.case_id == case_id)).all()
    primary = [m for m in media if m.role == "primary"]
    locs = s.exec(select(Location).where(Location.case_id == case_id, Location.dismissed == False)).all()  # noqa: E712
    runs = s.exec(select(AnalysisRun).where(AnalysisRun.case_id == case_id)).all()
    gen = load_genealogy(case_id)
    gaps = []

    pending = [l for l in locs if l.state == "pending"]
    if pending:
        gaps.append(_gap("capture_pending", f"{len(pending)} known URL(s) have not been captured yet",
                         "Posts can be deleted at any moment; an uncaptured URL is not evidence.",
                         "Capture each URL now (screenshot, HTML, media, hashes).", "you", "low", 5, 2.0,
                         action="capture"))
    if not locs:
        gaps.append(_gap("no_locations", "Where has the media appeared online?",
                         "Without captured locations there is no propagation evidence, account or timeline.",
                         "Add every URL where you or others have seen the media (posts, forwards, channels).", "you",
                         "low", 5, 1.5, action="add_urls"))
    removed_uncaptured = [l for l in locs if l.state == "removed" and not l.screenshot_sha]
    if removed_uncaptured:
        gaps.append(_gap("removed_uncaptured", f"{len(removed_uncaptured)} URL(s) were removed before capture",
                         "Their content cannot be shown to authorities unless another record exists.",
                         "Check Internet Archive snapshots; collect screenshots/screen recordings others made, with "
                         "the device and time they were taken.", "you", "medium", 3))

    families = [f for f in gen.get("families", []) if len(f["members"]) > 1]
    gaps.append(_gap("origin_unproven", "Who first uploaded or created the media, and from where?",
                     "The tree shows the earliest DISCOVERED copy, not the true original. Creation and first upload "
                     "remain unproven." if families else "No copy family has been established yet.",
                     "Platform upload records and account details for the earliest discovered account, obtained by "
                     "police through a lawful request; and preservation requests before data retention lapses.",
                     "police → platform", "high", 5, 1.2))

    for m in primary:
        ex = media_explanation(s, case_id, m.id)
        det_runs = [r for r in runs if r.media_id == m.id and r.component in
                    ("ufd_clip", "api_hive", "api_sightengine", "api_reality_defender")]
        ok_det = [r for r in det_runs if r.outcome == "ok"]
        if len(ok_det) < 2 and ex["level"] != "Strong":
            missing = [r.component for r in det_runs if r.outcome != "ok"]
            gaps.append(_gap(f"detector_corroboration_{m.id}",
                             f"Is “{m.label}” synthetic/manipulated? Current indicators: {ex['level']}",
                             f"Only {len(ok_det)} learned detector family ran; one detector alone cannot establish "
                             "manipulation, and none can establish authenticity.",
                             "Run a second, independent detector family (install the local model, or a commercial API "
                             "with your consent), and/or obtain an expert forensic examination.",
                             "you / forensic expert", "low" if missing else "medium", 4, 1.0, action="detectors"))
        if not any(mm.role == "reference" for mm in media):
            gaps.append(_gap("reference_media", "Does the depicted person's authentic appearance/voice differ from the media?",
                             "Authentic reference photos/voice recordings of the depicted person allow face/voice "
                             "comparison and expose inconsistencies (moles, teeth, accent).",
                             "Upload 3–5 authentic photos or voice notes of the depicted person, with consent, "
                             "marked as reference material.", "you", "low", 3, 1.0, action="upload_reference"))
            break
    if any(m.modality == "video" for m in primary):
        gaps.append(_gap("lipsync", "Do lip movements match the audio?",
                         "Voice-swapped videos often show subtle lip-sync drift; this was not assessed automatically.",
                         "Expert frame-by-frame review, or an audio-visual sync model.", "forensic expert", "medium", 2))

    accounts = {l.account_handle for l in locs if l.account_handle}
    if accounts:
        gaps.append(_gap("account_identity", f"Who operates the {len(accounts)} account(s) that posted the media?",
                         "Handles and display names prove nothing about the person behind them.",
                         "Subscriber/login records from each platform — only via police request. Ask the police to send "
                         "data-preservation requests early.", "police → platform", "high", 4, 1.0))
    comp = s.get(Complaint, case_id)
    if not comp or comp.state != "submitted":
        gaps.append(_gap("not_reported", "Has the incident been reported?",
                         "Platform takedown and police action start only after a complaint; delays risk evidence loss "
                         "and further spread.", "Review and submit the prepared complaint (NCRP / platform).", "you", "low",
                         4, 1.5 if locs else 1.0, action="complaint"))
    mon = s.get(MonitorConfig, case_id)
    if not mon or not mon.enabled:
        gaps.append(_gap("monitoring_off", "Will new copies or re-uploads be noticed?",
                         "Deepfake incidents recur; removals are often followed by re-uploads.",
                         "Enable continuous monitoring for this case.", "you", "low", 2, 1.0, action="monitor"))
    if case.safety.get("intimate"):
        gaps.append(_gap("stopncii", "Can re-uploads be blocked on participating platforms?",
                         "StopNCII.org hashes the image on your own device; participating platforms block matches.",
                         "Create a StopNCII case from your own device (the image never leaves it).", "you", "low", 4, 1.3))
    gaps.sort(key=lambda g: -g["score"])
    return gaps
