"""Takedown & complaint navigator + tracker.

Builds, from the case, the list of things the USER should do next to get content removed and a complaint
registered — each with exact steps, the right official destination, prepared text and the deadline that applies
once they have reported. It records what the user did (reference numbers, dates, replies) and flags what is
overdue. It never files anything and never claims an integration with NCRP or any platform.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from sqlmodel import Session, select

from ..core.db import vault_for
from ..core.models import Case, Location, Report, TimelineNote
from ..propagation.discover import relations
from .complaint import get_or_create, platform_routes

REFS = Path(__file__).parent / "refs"
OPEN = {"planned", "submitted", "acknowledged", "action_taken", "escalated"}
IST = timezone(timedelta(hours=5, minutes=30))


@lru_cache(maxsize=1)
def clocks() -> dict:
    return yaml.safe_load((REFS / "takedown_clocks.yaml").read_text(encoding="utf-8"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def platform_clock(case: Case) -> dict:
    c = clocks()["clocks"]
    if case.incident_type in ("ncii", "sextortion", "impersonation_fraud") or case.safety.get("intimate"):
        return {**c["intimate_morphed"], "legal": True}
    return {**c["grievance_resolution"], "legal": True, "also": c["other_content"]}


def _due(submitted: datetime, clock: dict) -> datetime:
    return submitted + (timedelta(hours=clock["hours"]) if "hours" in clock else timedelta(days=clock["days"]))


def view(r: Report, locs: dict[str, Location] | None = None) -> dict:
    now = _now()
    due = _aware(r.due_at)
    d = r.model_dump(mode="json")
    d["open"] = r.status in OPEN
    d["overdue"] = bool(due and r.status in ("submitted", "acknowledged", "action_taken") and due < now)
    d["seconds_left"] = int((due - now).total_seconds()) if due and r.status in OPEN else None
    d["urls"] = [locs[i].url for i in (r.location_ids or []) if locs and i in locs]
    return d


def escalation_text(case: Case, r: Report, urls: list[str]) -> str:
    sub = _aware(r.submitted_at)
    when = sub.astimezone(IST).strftime("%d %b %Y %H:%M IST") if sub else "[date]"
    kind = ("intimate/sexual material that was artificially created or morphed without my consent. "
            if case.incident_type in ("ncii", "sextortion") or case.safety.get("intimate") else
            "synthetic media that impersonates or misrepresents me. ")
    ref = r.reference or "[reference number]"
    return (f"To: The Grievance Officer, {r.target}\n"
            f"Subject: Escalation - complaint {ref} not resolved within the prescribed time\n\n"
            f"I reported the content listed below on {when} (reference: {ref}). The time allowed under the "
            f"Information Technology (Intermediary Guidelines and Digital Media Ethics Code) Rules, 2021 (as amended) "
            f"has passed and the content is still available.\n\nURLs:\n" + "\n".join(f"- {u}" for u in urls) +
            "\n\nThe content is " + kind +
            "Please remove it immediately, confirm in writing what action was taken, and preserve the related account "
            "and upload records. If this is not resolved I will appeal to the Grievance Appellate Committee (gac.gov.in) "
            "and report to the police.\n\n[Your name and contact details]")


def build_plan(s: Session, case_id: str) -> dict:
    case = s.get(Case, case_id)
    routes = platform_routes()
    f = get_or_create(s, case_id).fields
    rel = relations(s, case_id)
    locs_all = s.exec(select(Location).where(Location.case_id == case_id, Location.dismissed == False)).all()  # noqa: E712
    locs = {l.id: l for l in locs_all}
    reports = s.exec(select(Report).where(Report.case_id == case_id).order_by(Report.created_at)).all()
    by_key = {r.key: r for r in reports}
    pol_minor = bool(case.safety.get("minors_possible"))
    intimate = bool(case.safety.get("intimate")) or case.incident_type in ("ncii", "sextortion")
    clock = platform_clock(case)
    actions: list[dict] = []

    def act(key, kind, title, why, steps, links=(), text=None, loc_ids=(), clock_=None, priority=5, target=""):
        r = by_key.get(key)
        actions.append({"key": key, "kind": kind, "title": title, "why": why, "steps": list(steps),
                        "links": list(links), "prepared_text": text, "location_ids": list(loc_ids), "clock": clock_,
                        "urls": [locs[i].url for i in loc_ids if i in locs],
                        "priority": priority, "target": target, "report": view(r, locs) if r else None,
                        "done": bool(r and r.status in ("submitted", "acknowledged", "action_taken", "removed", "closed"))})

    # 0. money first
    if case.incident_type in ("impersonation_fraud", "voice_clone_scam", "sextortion"):
        act("bank_1930", "bank", "If money was paid or demanded: call 1930 and your bank NOW",
            "Speed matters most for freezing a transfer. This does not depend on proving anything is fake.",
            ["Call 1930 (National Cyber Crime Helpline) and give the transaction details.",
             "Call your bank's fraud helpline and ask them to block/flag the beneficiary account.",
             "Do not send more money. Keep the chat, call log and transaction IDs unedited."],
            [{"label": "Call 1930", "url": "tel:1930"}], priority=0, target="1930 / bank")

    # 1. the police complaint
    route = f["route"]
    act("ncrp", "ncrp", "Register your complaint on the National Cyber Crime Reporting Portal", route["description"],
        ["Open cybercrime.gov.in and choose "
         + ("'Report Women/Child Related Crime' (you may report anonymously)." if route["key"] == "women_child"
            else "'Report Other Cyber Crime' (you will register with an Indian mobile number and OTP)."),
         "Copy each prepared block from the Complaint page into the matching portal field.",
         "Attach the files marked 'portal-ready'. Keep the full evidence package for the police.",
         "After submitting, write down the acknowledgement number and record it here.",
         "A portal complaint is not an FIR. You can also go to your local police station (a Zero FIR can be filed at any station)."],
        [{"label": "Open cybercrime.gov.in", "url": route["portal"]}, {"label": "Prepared complaint", "url": "@complaint"}],
        priority=1, target="NCRP (Ministry of Home Affairs)",
        clock_={"days": clocks()["suggested_followups"]["ncrp_check_days"], "legal": False,
                "label": "Suggested time to check the complaint status (not a legal deadline)"})

    # 2. one report per platform (only pages that belong in the case record)
    plat_locs: dict[str, list[Location]] = {}
    for l in locs_all:
        in_record = rel.get(l.id, {}).get("status") in ("seed", "verified_copy") or l.discovered_via == "manual"
        if in_record and l.state != "removed":
            plat_locs.setdefault(l.platform or l.domain, []).append(l)
    prepared = {p["platform"]: p for p in f.get("platform_reports", [])}
    for plat, ls in sorted(plat_locs.items()):
        info = routes["platforms"].get(plat, routes["platforms"]["Website"])
        pr = prepared.get(plat)
        links = [{"label": f"{plat} help centre", "url": info["help"]}] if info.get("help") else []
        links += [{"label": "Open the post", "url": l.url} for l in ls[:3]]
        act(f"platform:{plat}", "platform", f"Report the post(s) to {plat}",
            f"{len(ls)} live location(s) on {plat}. A report to the platform is what actually gets content taken down, "
            f"and its reply starts a deadline you can enforce.",
            [info["report"], "Paste the prepared text below into the report form (choose the 'intimate / non-consensual' "
             "or 'impersonation' reason if offered).",
             "Save the confirmation / ticket number the platform shows or emails you, and record it here.",
             info.get("grievance") or "If the platform does not act in time, escalate to its India Grievance Officer."],
            links, text=pr["text"] if pr else None, loc_ids=[l.id for l in ls], clock_=clock, priority=2, target=plat)

    # 3. special routes
    if intimate and not pol_minor:
        act("stopncii", "stopncii", "Block re-uploads with StopNCII.org",
            "StopNCII creates a digital fingerprint of the image on YOUR device (the image is never uploaded) so "
            "participating platforms can block copies. It accepts AI-made intimate images of you if you are 18+.",
            ["Open StopNCII.org on your own phone or computer.",
             "Select the image from your device - it is fingerprinted locally.",
             "Save the case number and PIN it gives you; record the case number here."],
            [{"label": "Open StopNCII.org", "url": "https://stopncii.org"}], priority=3, target="StopNCII.org")
    if pol_minor:
        act("minor_report", "ncrp", "Report to NCRP's child-protection route - URLs only",
            "A minor may be depicted. Do not download, store or forward the material; report only the web addresses.",
            ["Open cybercrime.gov.in -> 'Report Women/Child Related Crime' -> report anonymously if you prefer.",
             "Give the URLs. Do not upload the images.", "Childline 1098 or 112 if a child is in danger."],
            [{"label": "Open cybercrime.gov.in", "url": route["portal"]}], priority=0, target="NCRP")

    # 4. escalation for platform reports that passed their deadline
    for r in reports:
        v = view(r, locs)
        if r.kind == "platform" and v["overdue"]:
            urls = [locs[i].url for i in (r.location_ids or []) if i in locs]
            act(f"escalate:{r.id}", "grievance_officer", f"{r.target} has not acted in time - write to its Grievance Officer",
                f"Your report ({r.reference or 'no reference recorded'}) passed its deadline and the content may still be live.",
                ["Send the text below to the platform's India Grievance Officer (contact details are in its help centre).",
                 "Keep the sent email and any reply. Record the date you sent it here.",
                 "If the Grievance Officer rejects it or stays silent, appeal to the Grievance Appellate Committee (gac.gov.in)."],
                [{"label": "Grievance Appellate Committee", "url": "https://gac.gov.in"}],
                text=escalation_text(case, r, urls), loc_ids=list(r.location_ids or []),
                clock_={**clocks()["clocks"]["gac_appeal"], "legal": True}, priority=1,
                target=f"{r.target} Grievance Officer")

    actions.sort(key=lambda a: (a["done"], a["priority"]))
    open_reports = [view(r, locs) for r in reports if r.status in OPEN]
    return {
        "actions": actions,
        "reports": [view(r, locs) for r in reports],
        "summary": {"total": len(actions), "done": sum(a["done"] for a in actions),
                    "overdue": sum(1 for r in open_reports if r["overdue"]), "open": len(open_reports),
                    "removed": sum(1 for r in reports if r.status == "removed")},
        "clock_sources": {"as_of": clocks()["as_of"], "note": clocks()["verify_note"], "sources": clocks()["sources"]},
        "disclaimer": "This is a guide to the official routes, not legal advice. You submit every report yourself; this "
                      "app only prepares the material and keeps your record.",
    }


def upsert_report(s: Session, case_id: str, body: dict) -> Report:
    """Record what the user did. Creates or updates the Report for a plan action (by key)."""
    case = s.get(Case, case_id)
    key = body.get("key") or ""
    r = s.exec(select(Report).where(Report.case_id == case_id, Report.key == key)).first() if key else None
    r = r or Report(case_id=case_id, key=key, kind=body.get("kind", "other"), title=body.get("title", key))
    for fld in ("kind", "title", "target", "reference", "notes"):
        if body.get(fld) is not None:
            setattr(r, fld, body[fld])
    if body.get("location_ids") is not None:
        r.location_ids = body["location_ids"]
    status = body.get("status")
    if status and status != r.status:
        r.status = status
        r.history = [*(r.history or []), {"at": _now().isoformat(), "status": status, "note": body.get("note", "")}]
    if status == "submitted" and not r.submitted_at:
        r.submitted_at = datetime.fromisoformat(body["submitted_at"]) if body.get("submitted_at") else _now()
        clk = body.get("clock") or (platform_clock(case) if r.kind == "platform" else None)
        if clk:
            r.due_at = _due(_aware(r.submitted_at), clk)
            r.clock_basis = clk.get("basis") or clk.get("label", "")
        elif r.kind == "ncrp":
            r.due_at = _due(_aware(r.submitted_at), {"days": clocks()["suggested_followups"]["ncrp_check_days"]})
            r.clock_basis = "Suggested time to check the complaint status (not a legal deadline)"
    if body.get("due_at"):
        r.due_at = datetime.fromisoformat(body["due_at"])
    r.updated_at = _now()
    s.add(r)
    if status:
        kind = {"submitted": "report_submitted", "removed": "removal_confirmed", "acknowledged": "platform_response",
                "action_taken": "platform_response", "rejected": "platform_response"}.get(status, "user_note")
        s.add(TimelineNote(case_id=case_id, at=_now(), title=f"{r.title}: {status.replace('_', ' ')}",
                           detail=(f"Reference: {r.reference}. " if r.reference else "") + (body.get("note") or ""),
                           kind=kind, data={"report_id": r.id, "status": status}))
    s.commit()
    vault_for(case_id).custody.append("report.recorded_by_user", actor="investigator",
                                      details={"key": r.key, "kind": r.kind, "status": r.status, "reference": r.reference})
    return r
