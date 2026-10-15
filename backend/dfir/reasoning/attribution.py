"""Attribution leads in a fixed five-part template:
Discovered → Evidence strength → What it suggests → What remains unknown → Evidence required.
Language is linted so that no lead reads as an accusation."""
from __future__ import annotations

from sqlmodel import Session

from ..core.claims import sanitize_text
from ..graph.analytics import analyse
from ..graph.build import build_graph
from ..pipeline import load_genealogy

PLATFORM_RECORDS = ("Account registration details, login IP logs and upload records held by the platform. These are "
                    "obtainable only through a lawful request made by the police/investigating agency to the platform.")


def _strength(n_verified: int, n_leads: int, early: bool = False) -> str:
    """Strength of the distribution link (captured posting), not of any authorship inference."""
    if n_verified >= 1 and early:
        return "Strong"
    if n_verified >= 1:
        return "Moderate"
    return "Weak"


def build_leads(s: Session, case_id: str) -> dict:
    G = build_graph(s, case_id)
    gen = load_genealogy(case_id)
    an = analyse(G, gen)
    leads = []
    for i, a in enumerate(an["ranked_accounts"][:8]):
        discovered = []
        for url in a["urls"]:
            nd = G.nodes[url]
            discovered.append({"text": f"Posted a copy at {nd.get('label')} (page state at last check: {nd.get('state')})",
                               "status": "Verified"})
        for r in a["reasons"]:
            discovered.append({"text": r[0].upper() + r[1:], "status": "Inferred" if "posted" in r and "look-alike" not in r else "Lead"})
        n_ver = sum(1 for d in discovered if d["status"] == "Verified")
        n_lead = sum(1 for d in discovered if d["status"] == "Lead")
        early = any("earliest" in r or "closest-to-source" in r for r in a["reasons"])
        suggests = ("This account is among the earliest discovered distributors of the media and is the highest-value "
                    "lead for finding the source." if a["score"] >= 3 else
                    "This account distributed the media; its role relative to the source is unclear.")
        leads.append({
            "id": f"lead_acct_{i}", "kind": "account", "subject": f"{a['label']} on {a['platform']}",
            "node": a["node"], "priority": i + 1, "score": a["score"],
            "discovered": discovered,
            "evidence_strength": _strength(n_ver, n_lead, early),
            "strength_scope": "Strength of the evidence that this account distributed the media, not that it created it.",
            "suggests": sanitize_text(suggests),
            "unknown": ["Who operates this account", "Whether the account made the media or reposted it from elsewhere",
                        "Whether earlier (private, deleted or undiscovered) copies exist"],
            "required": [PLATFORM_RECORDS,
                         "The account's earliest post of this media, captured before deletion",
                         "Any original file with intact metadata/C2PA held by this account"],
        })
    for n, d in G.nodes(data=True):
        if d.get("type") == "generator":
            linked = [u for u, _ in G.in_edges(n)]
            statuses = {ed.get("status") for _, _, ed in G.in_edges(n, data=True)}
            strong = "Verified" in statuses
            leads.append({
                "id": f"lead_{n}", "kind": "generation_tool", "subject": f"Generation tool family: {d['label']}",
                "node": n, "priority": 50, "score": 0,
                "discovered": [{"text": f"{len(linked)} media item(s) carry this provenance/generator signal",
                                "status": "Verified" if strong else "Lead"}],
                "evidence_strength": "Strong" if strong else "Weak",
                "suggests": f"The media was likely produced or processed with {d['label']}.",
                "unknown": ["Which user operated the tool", "When and where it was run"],
                "required": ["Account/usage records from the tool's provider (lawful request), if the provider keeps them",
                             "Matching project/source files on a seized device (police forensic examination)"],
            })
        if d.get("type") in ("device", "encoder"):
            linked = [u for u, _ in G.in_edges(n)]
            if len(linked) >= 2 or d.get("type") == "device":
                leads.append({
                    "id": f"lead_{n}", "kind": "device_signature", "subject": f"Shared technical signature: {d['label']}",
                    "node": n, "priority": 60, "score": 0,
                    "discovered": [{"text": f"{len(linked)} media item(s) share this {'device metadata' if d['type'] == 'device' else 'encoder/table signature'}",
                                    "status": "Verified"}],
                    "evidence_strength": "Weak",
                    "suggests": "Items may have passed through the same device, app or platform pipeline.",
                    "unknown": ["Whether the signature is specific (one device) or generic (millions of devices/apps)"],
                    "required": ["Sensor-noise (PRNU) comparison against reference images from a suspected device, "
                                 "performed by a forensic lab"],
                })
    origin = []
    for fam in gen.get("families", []):
        if len(fam["members"]) > 1:
            origin.append({"root": fam["root"], "earliest_observed": fam.get("earliest_observed"),
                           "members": len(fam["members"])})
    leads.sort(key=lambda l: l["priority"])
    return {"leads": leads, "origin": origin, "ranked_accounts": an["ranked_accounts"],
            "principle": "Leads are starting points for lawful investigation, not findings of responsibility. "
                         "Nothing here establishes who created the media."}
