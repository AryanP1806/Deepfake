"""“What should I do now?” — the safest, highest-value next action given evidence and uncertainty."""
from __future__ import annotations

from sqlmodel import Session, select

from ..core.models import Case, Complaint, Location, MonitorConfig
from .gaps import analyse_gaps

NCRP = "https://cybercrime.gov.in"

INCIDENT_GUIDANCE = {
    "ncii": {
        "urgent": True,
        "dont": ["Do not contact or confront the accounts posting it.",
                 "Do not forward the media to friends 'for awareness' — every forward is another copy."],
        "extra": [
            ("report", "Report each post to the platform as non-consensual intimate / morphed imagery",
             "Platforms prioritise complaints from the depicted person about intimate or morphed images."),
            ("report", "Create a StopNCII.org case from your own device",
             "Blocks re-uploads on participating platforms without sharing the image."),
        ],
    },
    "sextortion": {
        "urgent": True,
        "dont": ["Do not pay — payment almost always leads to further demands.",
                 "Do not delete the chat; preserve it (screenshots + export) before blocking.",
                 "Do not negotiate or reply further."],
        "extra": [("report", "Call 1930 (National Cyber Crime Helpline) if money was paid or demanded",
                   "Fast reporting of financial fraud can help freeze transfers.")],
    },
    "impersonation_fraud": {
        "urgent": True,
        "dont": ["Do not transfer money or share OTPs based on the video/voice.",
                 "Warn your contacts through a channel you control, without re-sharing the fake."],
        "extra": [("report", "Call 1930 immediately if any money was transferred", "Speed matters for freezing funds."),
                  ("report", "Report the impersonating account to the platform", "Impersonation is a removal ground on "
                                                                                 "major platforms.")],
    },
    "voice_clone_scam": {
        "urgent": True,
        "dont": ["Do not act on voice instructions without calling the person back on a known number."],
        "extra": [("report", "Call 1930 if money was transferred", "Speed matters for freezing funds.")],
    },
    "political_defamation": {
        "urgent": False,
        "dont": ["Do not publicly accuse specific accounts of creating it — attribution is unproven."],
        "extra": [("report", "Report the posts to platforms as manipulated media / impersonation",
                   "Most platforms label or remove manipulated media."),
                  ("escalate", "Consult a lawyer before any public statement naming individuals",
                   "Public allegations without proof carry legal risk.")],
    },
    "other": {"urgent": False, "dont": [], "extra": []},
}


def next_action(s: Session, case_id: str) -> dict:
    case = s.get(Case, case_id)
    locs = s.exec(select(Location).where(Location.case_id == case_id)).all()
    comp = s.get(Complaint, case_id)
    mon = s.get(MonitorConfig, case_id)
    gaps = analyse_gaps(s, case_id)
    g = INCIDENT_GUIDANCE.get(case.incident_type, INCIDENT_GUIDANCE["other"])
    pending = [l for l in locs if l.state == "pending"]
    live = [l for l in locs if l.state in ("live", "changed")]
    reported = bool(comp and comp.state == "submitted")

    checklist = []
    if case.safety.get("minors_possible"):
        primary = {"title": "Report to NCRP under 'Report Women/Child Related Crime' now",
                   "why": "Possible involvement of a minor. Do not download, store or share the material; report the "
                          "URLs only. Anonymous reporting is available for child sexual abuse material.",
                   "link": NCRP, "action": "complaint"}
    elif pending:
        primary = {"title": f"Preserve evidence: capture the {len(pending)} uncaptured URL(s) now",
                   "why": "Posts can disappear at any moment, and evidence of a removed post is far weaker. "
                          "Capturing takes about a minute per URL.", "action": "capture"}
    elif not locs:
        primary = {"title": "Add the URLs where the media has appeared",
                   "why": "Spread, accounts and the timeline all depend on captured locations.", "action": "add_urls"}
    elif not reported:
        primary = {"title": "Review and submit the prepared complaint",
                   "why": f"Evidence for {len(locs)} location(s) is preserved. "
                          + ("This incident type is urgent. " if g["urgent"] else "")
                          + "Reporting starts platform takedown and police action.", "link": NCRP,
                   "action": "complaint"}
    elif live:
        primary = {"title": f"Follow up: {len(live)} reported URL(s) are still live",
                   "why": "Re-report each URL to the platform with your NCRP acknowledgement number; escalate to the "
                          "platform's Grievance Officer if there is no action.", "action": "escalate"}
    else:
        primary = {"title": "Keep monitoring for re-uploads",
                   "why": "Known copies are down; re-uploads are common in the following weeks.", "action": "monitor"}

    checklist.append({"phase": "Preserve", "title": "Capture every known URL (screenshot, HTML, media, hashes)",
                      "done": bool(locs) and not pending, "why": "Evidence before it disappears."})
    checklist.append({"phase": "Preserve", "title": "Export the evidence package and keep a copy offline",
                      "done": False, "why": "Hash-verified package with custody log for police/court."})
    for phase, title, why in g["extra"]:
        checklist.append({"phase": phase.capitalize(), "title": title, "done": False, "why": why})
    checklist.append({"phase": "Report", "title": "Submit the NCRP complaint (cybercrime.gov.in) or call 1930",
                      "done": reported, "why": "Starts official action; note the acknowledgement number here."})
    checklist.append({"phase": "Escalate", "title": "If platforms do not act, escalate to their Grievance Officer, then "
                                                    "the Grievance Appellate Committee",
                      "done": False, "why": "Formal escalation route for intermediary complaints in India."})
    checklist.append({"phase": "Monitor", "title": "Enable monitoring for re-uploads and changes",
                      "done": bool(mon and mon.enabled), "why": "Incidents recur after removal."})
    return {"primary": primary, "dont": g["dont"], "checklist": checklist, "top_gaps": gaps[:3],
            "disclaimer": "This is process guidance, not legal advice. For legal decisions consult a lawyer or the police."}
