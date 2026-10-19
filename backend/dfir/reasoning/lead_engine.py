"""The lead-generation engine: from posts by several accounts to ranked, defensible investigative leads.

What it does, in the order an investigator would:
  1. finds the media versions (identical files, crops, mirrors, re-encodes...) and how each differs from its neighbours;
  2. reconstructs the earliest OBSERVED appearance of each version and of the whole family of copies;
  3. compares timestamps, captions, shared links, visible handles, look-alike handles and shared technical artefacts;
  4. draws a graph that keeps what was *observed* apart from what is only a *hypothesis*;
  5. emits leads, each with its evidence, honest confidence, alternative explanations, and the authorised evidence that
     would confirm or disprove it.

Hard rules (enforced in the wording and the scoring):
  * Several accounts sharing the same fake does not show they know each other. Sharing the same FILE is expected of any
    repost and counts for almost nothing; a relationship needs several *independent kinds* of distinctive evidence.
  * Four different roles are never merged: ORIGINAL CREATOR (never established from public copies), EDITOR (never named,
    only "first known uploader of an altered version"), FIRST KNOWN UPLOADER (of what was found), DISTRIBUTOR.
  * "Earliest observed" means earliest among what this search found. Unseen, deleted or private posts may be earlier.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations

from rapidfuzz import fuzz
from sqlmodel import Session, select

from ..core.claims import sanitize_text
from ..core.db import case_dir, load_claims
from ..core.models import Location, MatchEdge, Media
from ..propagation.discover import relations
from ..propagation.spread import describe_ops
from .source import all_prnu

URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.I)
TAG_RE = re.compile(r"#(\w{2,40})")
EDIT_OPS = {"localized_change", "text_overlay_added", "crop", "crop+reframe", "mirror", "rotate", "downscale", "caption", "border"}

ROLE_GLOSSARY = [
    {"role": "Original creator", "meaning": "Whoever first made the media.",
     "status": "Not established. Public copies cannot show who made the media; a signed provenance record would only show who signed it."},
    {"role": "Editor", "meaning": "Whoever altered an existing version (crop, overlay, face swap, caption).",
     "status": "Never named. The engine only reports who was the first known uploader of an altered version — that account may have edited it, or received it from someone who did."},
    {"role": "First known uploader", "meaning": "The account whose post of a version or family is the earliest one this search observed.",
     "status": "Observed, with its time source stated. Unseen, deleted or private earlier posts may exist."},
    {"role": "Distributor", "meaning": "An account that posted after the media already appeared elsewhere.",
     "status": "Observed. Says nothing about intent or about knowing any other account."},
]
PRINCIPLE = ("Leads are starting points for lawful investigation, not findings. Several accounts sharing the same media does not show "
             "that they know each other: independent reposters, aggregators and platform share features produce exactly this pattern.")

AUTHORISED = {
    "upload_logs": "Platform upload records for the post: exact upload time, the IP address and device used (lawful request by the police to the platform).",
    "registration": "Account registration data (email, phone, creation time and IP) held by the platform (lawful request).",
    "original_file": "The file as originally uploaded, before the platform re-encoded it, with its metadata (platform, lawful request).",
    "repost_graph": "The platform's own record of who shared or re-posted whom (platform, lawful request).",
    "messages": "Direct messages or group membership between the accounts (court order / lawful request; never obtainable from public pages).",
    "device": "Examination of a seized device or camera against the files (police forensic examination).",
    "archive": "An earlier archived capture of either post (Internet Archive / other archives: public, no authority needed).",
    "provider": "Account and usage records from the AI tool's provider (lawful request).",
}


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _norm_caption(t: str) -> str:
    t = URL_RE.sub(" ", t or "").lower()
    t = TAG_RE.sub(" ", t)
    return re.sub(r"[^\w\s]", " ", re.sub(r"\s+", " ", t)).strip()


def _norm_link(u: str) -> str:
    u = re.sub(r"[?#].*$", "", u.rstrip(".,;:!"))
    return re.sub(r"^https?://(www\.)?", "", u).rstrip("/").lower()


def _ist(d: datetime | None) -> str:
    """Readable time for evidence text (the structured fields keep ISO-8601)."""
    from datetime import timedelta
    return d.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y %H:%M IST") if d else "unknown time"


def _fmt(d: datetime | None) -> str | None:
    return d.astimezone(timezone.utc).isoformat() if d else None


def _id(*parts) -> str:
    return "lead_" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _gap(sec: float) -> str:
    sec = abs(sec)
    return f"{sec / 86400:.1f} days" if sec >= 172800 else f"{sec / 3600:.1f} hours" if sec >= 7200 else f"{sec / 60:.0f} minutes" if sec >= 120 else f"{sec:.0f} seconds"


def _wayback_first(case_id: str) -> dict[str, datetime]:
    p = case_dir(case_id) / "enrichment.json"
    out: dict[str, datetime] = {}
    if not p.exists():
        return out
    try:
        wb = json.loads(p.read_text(encoding="utf-8")).get("wayback", {})
    except json.JSONDecodeError:
        return out
    for url, snaps in wb.items():
        if isinstance(snaps, list) and snaps and snaps[0].get("timestamp"):
            try:
                out[url] = datetime.strptime(str(snaps[0]["timestamp"])[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return out


def build_lead_engine(s: Session, case_id: str) -> dict:
    rel = relations(s, case_id)
    all_locs = s.exec(select(Location).where(Location.case_id == case_id, Location.dismissed == False)).all()  # noqa: E712
    locs = [l for l in all_locs if rel.get(l.id, {}).get("status") in ("seed", "verified_copy") or l.discovered_via == "manual"]
    media = {m.id: m for m in s.exec(select(Media).where(Media.case_id == case_id)).all()}
    edges = s.exec(select(MatchEdge).where(MatchEdge.case_id == case_id)).all()
    wb = _wayback_first(case_id)

    # ---- posts ------------------------------------------------------------------------------------------------
    posts: list[dict] = []
    for l in locs:
        mids = [i for i in rel.get(l.id, {}).get("media_ids", []) if i in media]
        handle = (l.account_handle or "").lstrip("@").strip()
        key = f"{(l.platform or l.domain or 'web').lower()}:{handle.lower()}" if handle else f"site:{l.domain or l.url}"
        pub = _aware(l.published_at)
        cap = _aware(l.first_captured_at)
        caption = l.caption or (l.page_meta or {}).get("og:description") or ""
        posts.append({
            "id": l.id, "url": l.url, "platform": l.platform or l.domain, "domain": l.domain, "account": key,
            "account_label": ("@" + handle) if handle else (l.domain or l.url), "account_kind": "account" if handle else "site",
            "display": (l.account_display or "").strip(), "handle": handle.lower(), "caption": caption,
            "cap_norm": _norm_caption(caption), "tags": {t.lower() for t in TAG_RE.findall(caption)},
            "links": {_norm_link(u) for u in URL_RE.findall(caption)}, "media_ids": mids, "state": l.state,
            "time": pub, "time_basis": (l.published_source or "declared by the page") if pub else None,
            "captured": cap, "t_order": pub or cap, "order_basis": "declared" if pub else "capture",
            "archive_first": wb.get(l.url), "seed": l.seed, "relation": rel.get(l.id, {}).get("status")})
    posts_by_id = {p["id"]: p for p in posts}

    # ---- versions & clusters -----------------------------------------------------------------------------------
    version_ids = sorted({m for p in posts for m in p["media_ids"]})
    parent_of: dict[str, dict] = {}
    vedges: list[dict] = []
    uf = {v: v for v in version_ids}

    def find(x):
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x

    for e in edges:
        if e.a_media not in uf or e.b_media not in uf:
            continue
        ds = float(e.transforms.get("direction_score", 0) or 0)
        if e.verified:
            uf[find(e.a_media)] = find(e.b_media)
        direction = "a_to_b" if ds >= 1.5 else "b_to_a" if ds <= -1.5 else None
        ops = e.transforms.get("transforms_b_from_a" if ds >= 0 else "transforms_a_from_b", [])
        src, dst = (e.a_media, e.b_media) if ds >= 0 else (e.b_media, e.a_media)
        vedges.append({"a": e.a_media, "b": e.b_media, "verified": e.verified, "direction": direction, "from": src, "to": dst,
                       "ops": ops, "differences": describe_ops(ops), "edit_ops": sorted({o.get("op") for o in ops} & EDIT_OPS)})
        if direction and e.verified:
            cur = parent_of.get(dst)
            if cur is None or abs(ds) > cur["ds"]:
                parent_of[dst] = {"id": src, "ds": abs(ds), "differences": describe_ops(ops), "edit_ops": sorted({o.get("op") for o in ops} & EDIT_OPS)}
    cluster_of = {v: find(v) for v in version_ids}
    clusters: dict[str, list[str]] = defaultdict(list)
    for v, c in cluster_of.items():
        clusters[c].append(v)

    appear: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        for v in p["media_ids"]:
            appear[v].append(p)

    def earliest(plist: list[dict]) -> dict | None:
        ps = [p for p in plist if p["t_order"]]
        return min(ps, key=lambda p: p["t_order"]) if ps else None

    # label versions by earliest appearance (the suspect/primary media first)
    ordered_versions = sorted(version_ids, key=lambda v: ((media[v].role != "primary"), (earliest(appear[v]) or {}).get("t_order") or datetime.max.replace(tzinfo=timezone.utc)))
    vlabel = {v: ("Reported media" if media[v].role == "primary" else f"Version {i}") for i, v in enumerate(ordered_versions)}
    thumbs = {}
    from ..core.models import Artifact
    for a in s.exec(select(Artifact).where(Artifact.case_id == case_id, Artifact.kind == "thumb")).all():
        thumbs[a.media_id] = a.sha256

    # provenance / device facts per version (from claims + fingerprints)
    facts: dict[str, dict] = {}
    for v in version_ids:
        cl = load_claims(s, case_id, "decomposition", v)
        fp = media[v].fingerprints or {}
        dev = fp.get("device") or {}
        facts[v] = {
            "provenance": any(c.category in ("c2pa", "provenance") and c.status.value == "Verified" and c.data.get("generator_family") for c in cl),
            "c2pa": any(c.category == "c2pa" and c.status.value == "Verified" for c in cl),
            "serial": dev.get("serial"), "make_model": " ".join(filter(None, [dev.get("make"), dev.get("model")])) or None,
            "encoder": fp.get("encoder_signature"), "qtable": fp.get("qtable_hash"),
            "handles": {str(c.data["handle"]).lstrip("@").lower() for c in cl if c.category == "ocr" and c.data.get("handle")},
            "pixels": (media[v].width or 0) * (media[v].height or 0)}

    # ---- clusters: earliest observed appearance ---------------------------------------------------------------
    cluster_out, lead_list = [], []
    for ci, (root, vs) in enumerate(sorted(clusters.items(), key=lambda kv: -len(kv[1]))):
        cposts = {p["id"]: p for v in vs for p in appear[v]}.values()
        timed = sorted([p for p in cposts if p["t_order"]], key=lambda p: p["t_order"])
        first = timed[0] if timed else None
        second_acct = next((p for p in timed[1:] if p["account"] != (first or {}).get("account")), None)
        margin = (second_acct["t_order"] - first["t_order"]).total_seconds() if (first and second_acct) else None
        declared = bool(first and first["order_basis"] == "declared" and (second_acct is None or second_acct["order_basis"] == "declared"))
        tie = margin is not None and margin < 60
        cluster_out.append({"id": f"cluster_{ci + 1}", "versions": vs, "posts": len(list(cposts)), "accounts": sorted({p["account"] for p in cposts}),
                            "earliest_post": first["id"] if first else None, "margin_seconds": margin, "order_basis": "declared" if declared else "capture",
                            "tie": tie})
        if not first:
            continue
        # -- first known uploader lead
        if len(list(cposts)) >= 2 and len({p["account"] for p in cposts}) >= 1:  # a single post has nothing to be 'first' against
            archive_ok = bool(first["archive_first"] and (second_acct is None or first["archive_first"] < second_acct["t_order"]))
            if second_acct is None:
                conf, why = "low", "Only one account posting this family of copies was found, so there is nothing to compare it against."
            elif not declared:
                conf, why = "low", "At least one of the times is only the moment this app captured the page, not when it was posted, so the order is not reliable."
            elif tie:
                conf, why = "low", f"The next account posted only {_gap(margin)} later: too close to order reliably."
            elif archive_ok:
                conf, why = "high", f"Declared by the page and supported by an archive snapshot; the next account posted {_gap(margin)} later."
            else:
                conf, why = "moderate", f"Declared by the pages; the next account posted {_gap(margin)} later."
            ev = [{"text": f"{first['account_label']} on {first['platform']} posted at {_ist(first['t_order'])} ({first['time_basis'] or 'capture time only'})",
                   "kind": "observed", "group": "time", "refs": {"post": first["id"]}}]
            for p in timed[1:4]:
                ev.append({"text": f"{p['account_label']} on {p['platform']} posted {_gap((p['t_order'] - first['t_order']).total_seconds())} later ({p['time_basis'] or 'capture time only'})",
                           "kind": "observed", "group": "time", "refs": {"post": p["id"]}})
            if first["archive_first"]:
                ev.append({"text": f"Internet Archive holds a snapshot of that URL dated {_ist(first['archive_first'])}", "kind": "observed", "group": "archive", "refs": {"post": first["id"]}})
            lead_list.append(_lead(
                "first_uploader", f"{first['account_label']} ({first['platform']}) is the first known uploader of this media",
                [first["account"]], conf, 1.0, ev, why,
                alternatives=[
                    ("An earlier post exists that this search did not find (deleted, private, in a closed group, or on another platform).", "Search archives and other platforms for earlier copies; ask the platform for upload records."),
                    ("The page's publication time is wrong, rounded, in another time zone, or edited after posting.", "Compare with the platform's own upload timestamp and the Internet Archive."),
                    ("The account re-posted the media after another account removed or lost it.", "Check the account's post history and edit history for an earlier version of this post.")],
                confirm=["upload_logs", "archive", "original_file"], disprove=["archive", "upload_logs"],
                cautions=["First known uploader means earliest among what was found. It does not show who created the media or whether this account knows anyone else who shared it."],
                cluster=f"cluster_{ci + 1}"))

        # -- most-original-version hypothesis
        if len(vs) >= 2:
            def origin_score(v):
                f = facts[v]
                return (0.35 * (v not in parent_of) + 0.30 * (f["provenance"] or f["c2pa"] or bool(f["make_model"]))
                        + 0.25 * (f["pixels"] / max(1, max(facts[x]["pixels"] for x in vs))) + 0.10 * (media[v].role == "primary"))
            best = max(vs, key=origin_score)
            if origin_score(best) >= 0.55:
                where = earliest(appear[best])
                posters = sorted({p["account_label"] for p in appear[best]})
                lead_list.append(_lead(
                    "closest_to_source", f"{vlabel[best]} is the version closest to the source among those found",
                    [p["account"] for p in appear[best]], "low" if len(vs) == 2 else "moderate", 0.9,
                    [{"text": f"It is not derived from any other version found (the others are cropped, resized, re-compressed or edited forms of it)" if best not in parent_of else "It is the largest/most complete version", "kind": "observed", "group": "content", "refs": {"media": best}},
                     *([{"text": "It carries provenance/device metadata that the other versions lack", "kind": "observed", "group": "technical", "refs": {"media": best}}] if (facts[best]["provenance"] or facts[best]["make_model"]) else []),
                     {"text": f"Posted by: {', '.join(posters)}" + (f"; earliest at {_ist(where['t_order'])}" if where else ""), "kind": "observed", "group": "time", "refs": {"media": best}}],
                    "Closest to source is a statement about the FILES, not about who made them.",
                    alternatives=[("An even less-degraded version exists but was not found.", "Search for the largest/uncompressed version; ask the earliest poster for the original file."),
                                  ("The most complete version was posted by an account that received it from the creator.", "Compare posting times and ask the platform for upload records.")],
                    confirm=["original_file", "upload_logs", "device"], disprove=["archive"],
                    cautions=["The original creator is not established by this lead."], cluster=f"cluster_{ci + 1}"))

        # -- altered-version leads (first known uploader of an edited version)
        for v in vs:
            par = parent_of.get(v)
            if not par or not par["edit_ops"]:
                continue
            fp_ = earliest(appear[v])
            if not fp_:
                continue
            lead_list.append(_lead(
                "altered_version", f"{fp_['account_label']} ({fp_['platform']}) is the first known uploader of an altered version",
                [fp_["account"]], "moderate" if fp_["order_basis"] == "declared" else "low", 0.85,
                [{"text": f"{vlabel[v]} differs from {vlabel.get(par['id'], 'another version')}: " + "; ".join(par["differences"]), "kind": "observed", "group": "content", "refs": {"media": v}},
                 {"text": f"The earliest post of this altered version is by {fp_['account_label']} at {_ist(fp_['t_order'])} ({fp_['time_basis'] or 'capture time only'})", "kind": "observed", "group": "time", "refs": {"post": fp_["id"]}}],
                "The alteration is real; who performed it is not shown.",
                alternatives=[("An upstream account or app made the alteration and this account only passed it on.", "Search for earlier copies of THIS altered version."),
                              ("The alteration was automatic (platform crop, watermark or caption added by an app).", "Check whether other posts from the same app or platform show the same change.")],
                confirm=["original_file", "upload_logs", "device"], disprove=["archive"],
                cautions=["This lead does NOT say this account is the editor — only that it is the first known uploader of the altered version."],
                cluster=f"cluster_{ci + 1}"))

        # -- upload sequence
        chain_accts: list[dict] = []
        for p in timed:
            if not chain_accts or chain_accts[-1]["account"] != p["account"]:
                if all(c["account"] != p["account"] for c in chain_accts):
                    chain_accts.append(p)
        if len(chain_accts) >= 3 and all(p["order_basis"] == "declared" for p in chain_accts[:3]):
            hops = [(a, b) for a, b in zip(chain_accts, chain_accts[1:])]
            consistent = 0
            for a, b in hops:
                va, vb = (a["media_ids"] or [None])[0], (b["media_ids"] or [None])[0]
                if va and vb and (va == vb or any(e["verified"] and e["direction"] and e["from"] == va and e["to"] == vb for e in vedges)):
                    consistent += 1
            lead_list.append(_lead(
                "sequence", "Upload sequence: " + " → ".join(p["account_label"] for p in chain_accts[:5]),
                [p["account"] for p in chain_accts[:5]], "moderate" if consistent == len(hops) else "low", 0.7,
                [{"text": f"{a['account_label']} posted {_gap((b['t_order'] - a['t_order']).total_seconds())} before {b['account_label']}", "kind": "observed", "group": "time", "refs": {"post": a["id"]}} for a, b in hops[:4]] +
                [{"text": f"{consistent} of {len(hops)} hops are consistent with a repost chain (the later version is the same file or an altered form of the earlier one)", "kind": "hypothesis", "group": "content", "refs": {}}],
                "The order of posting is observed. A chain of copying between them is only a hypothesis.",
                alternatives=[("All of them copied from a common upstream source (a news site, meme page or the original file) at different times.", "Look for an earlier shared source; check each post for a 'via' / credit line."),
                              ("Reposts followed the platform's own share/quote feature, which reveals the real chain.", "Ask the platform for its repost graph.")],
                confirm=["repost_graph", "upload_logs"], disprove=["archive", "repost_graph"],
                cautions=["Accounts appearing one after another does not mean they are connected."], cluster=f"cluster_{ci + 1}"))

    # ---- account pair evidence --------------------------------------------------------------------------------
    by_acct: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        by_acct[p["account"]].append(p)
    prnu_pairs = [r for r in all_prnu(case_id) if r.get("pair") and r.get("status") == "consistent"]
    pair_leads = []
    for ka, kb in combinations(sorted(by_acct), 2):
        pa, pb = by_acct[ka], by_acct[kb]
        atoms: list[dict] = []

        def atom(group, w, distinct, text, direct=True, refs=None):
            atoms.append({"group": group, "w": w, "distinct": distinct, "text": text, "kind": "observed" if direct else "hypothesis", "refs": refs or {}})

        mia = {m for p in pa for m in p["media_ids"]}
        mib = {m for p in pb for m in p["media_ids"]}
        if mia & mib:
            atom("content", 0.15, False, "Both posted the identical file (same SHA-256). This is expected of ANY repost and shows no link by itself.")
        elif {cluster_of[m] for m in mia if m in cluster_of} & {cluster_of[m] for m in mib if m in cluster_of}:
            atom("content", 0.10, False, "Both posted versions of the same media (one is a crop, resize or re-encode of the other).")
        for x in pa:
            for y in pb:
                if len(x["cap_norm"]) >= 20 and len(y["cap_norm"]) >= 20:
                    if x["cap_norm"] == y["cap_norm"]:
                        atom("text", 0.5, True, f"Identical caption text (“{x['caption'][:70]}…”)", refs={"posts": [x["id"], y["id"]]})
                        break
                    r = fuzz.token_set_ratio(x["cap_norm"], y["cap_norm"])
                    if r >= 85:
                        atom("text", 0.3, True, f"Near-identical caption text ({r:.0f}% similar)", refs={"posts": [x["id"], y["id"]]})
                        break
            else:
                continue
            break
        shared_links = {l for p in pa for l in p["links"]} & {l for p in pb for l in p["links"]}
        if shared_links:
            atom("text", 0.5, True, "Both captions point to the same link: " + ", ".join(sorted(shared_links))[:90])
        tags = {t for p in pa for t in p["tags"]} & {t for p in pb for t in p["tags"]}
        if len(tags) >= 3:
            atom("text", 0.15, False, f"{len(tags)} hashtags in common (#{', #'.join(sorted(tags)[:4])})")
        best = None
        for x in pa:
            for y in pb:
                if x["time"] and y["time"]:
                    d = abs((x["time"] - y["time"]).total_seconds())
                    best = d if best is None or d < best else best
        if best is not None and best <= 300:
            atom("time", 0.3, True, f"Posted within {_gap(best)} of each other (both times declared by the pages)")
        elif best is not None and best <= 3600:
            atom("time", 0.12, False, f"Posted within {_gap(best)} of each other")
        # handle of one visible inside the other's media
        for (ka_, pa_), (kb_, pb_) in (((ka, pa), (kb, pb)), ((kb, pb), (ka, pa))):
            h = pa_[0]["handle"]
            if h and any(fuzz.ratio(h, vh) >= 90 for p in pb_ for m in p["media_ids"] for vh in facts[m]["handles"]):
                atom("identity", 0.5, True, f"{pa_[0]['account_label']}'s handle is visible inside a copy posted by {pb_[0]['account_label']} (read from the picture)")
        ha, hb = pa[0]["handle"], pb[0]["handle"]
        if ha and hb and fuzz.ratio(ha, hb) >= 85:
            same = pa[0]["platform"] == pb[0]["platform"]
            atom("identity", 0.4 if same else 0.15, same, f"Look-alike handles{' on the same platform' if same else ' on different platforms'} ({ha} / {hb})")
        if pa[0]["display"] and pa[0]["display"].lower() == pb[0]["display"].lower():
            atom("identity", 0.35, True, f"Same display name “{pa[0]['display']}”")
        sa = {facts[m]["serial"] for m in mia if facts[m]["serial"]}
        sb = {facts[m]["serial"] for m in mib if facts[m]["serial"]}
        if sa & sb:
            atom("technical", 0.8, True, f"Files from both carry the same camera serial number ({sorted(sa & sb)[0]}) — declared in metadata, so editable")
        pr = [r for r in prnu_pairs if ((r["pair"][0] in mia and r["pair"][1] in mib) or (r["pair"][1] in mia and r["pair"][0] in mib))
              and cluster_of.get(r["pair"][0]) != cluster_of.get(r["pair"][1])]  # copies of one picture share noise because of the picture
        if pr:
            atom("technical", 0.9, True, f"Sensor-noise (PRNU) comparison finds the two files consistent with one sensor (PCE {pr[0].get('pce')})", refs={"prnu": pr[0].get("pair")})
        ea, eb = {facts[m]["encoder"] for m in mia} - {None}, {facts[m]["encoder"] for m in mib} - {None}
        if ea & eb and not (mia & mib):
            atom("technical", 0.08, False, "Files share an encoder/processing signature (very common; usually the same app or platform)")
        if not atoms:
            continue
        groups: dict[str, float] = {}
        for a in atoms:
            groups[a["group"]] = max(groups.get(a["group"], 0), a["w"])
        score = 1.0
        for w in groups.values():
            score *= (1 - w)
        score = 1 - score
        distinct = {a["group"] for a in atoms if a["distinct"]}
        conf = "high" if (len(distinct) >= 3 and score >= 0.8) else "moderate" if (len(distinct) >= 2 and score >= 0.55) else "low" if (distinct and score >= 0.3) else None
        pair_leads.append((ka, kb, atoms, score, distinct, conf))

    alts_link = [("Both accounts independently re-posted a widely shared item. The same file and caption are expected of reposters.", "Look for the common upstream source and for other accounts sharing the identical caption."),
                 ("A common upstream source (a news site, meme page, or app share button) supplied both the file and the caption.", "Trace the caption and file to its earliest appearance."),
                 ("One of the accounts is an aggregator or automated repost bot.", "Check the account's posting rate and whether it posts the same items as many other accounts.")]
    for ka, kb, atoms, score, distinct, conf in sorted(pair_leads, key=lambda t: -t[3]):
        if not conf:
            continue
        la, lb = by_acct[ka][0]["account_label"], by_acct[kb][0]["account_label"]
        alts = list(alts_link)
        if "identity" in distinct:
            alts.append(("Look-alike or matching handles can be coincidence, a fan/parody account, or one party impersonating the other — in either direction.", "Compare account creation dates and profile history; ask the platform for registration data."))
        if "technical" in distinct:
            alts.append(("A shared device/encoder signature can come from a common app, platform pipeline or an edited/copied metadata field rather than a common person.", "Obtain the originally uploaded files; run a sensor-noise comparison with reference photos from a suspected device."))
        lead_list.append(_lead(
            "link", f"Possible link between {la} and {lb}", [ka, kb], conf, 0.8 * score + 0.1, [{k: v for k, v in a.items() if k != "w" and k != "distinct"} for a in atoms],
            f"{len(distinct)} independent kind(s) of distinctive evidence ({', '.join(sorted(distinct)) or 'none'}); content that is merely shared is deliberately not counted.",
            alternatives=alts, confirm=["registration", "upload_logs", "messages", "device"], disprove=["registration", "upload_logs", "archive"],
            cautions=["This is a hypothesis that the accounts are connected. It is NOT shown that they know each other, coordinate, or that either made the media."],
            score=round(score, 2)))

    # ---- generator tie-in (from the claims) --------------------------------------------------------------------
    from .generator import generator_attribution
    fam_seen: dict[str, list[str]] = defaultdict(list)
    for v in version_ids:
        ga = generator_attribution(load_claims(s, case_id, "decomposition", v) + [c for c in load_claims(s, case_id, "genealogy", v) if c.category == "family_provenance"])
        if ga["possible_family"] and ga["tier"] in ("established", "supported"):
            fam_seen[ga["possible_family"]].append(v)
    for fam, vs_ in fam_seen.items():
        lead_list.append(_lead(
            "generator", f"The media was probably made with a {fam} tool", sorted({p["account"] for v in vs_ for p in appear[v]}), "moderate", 0.5,
            [{"text": f"{len(vs_)} version(s) carry supporting evidence for {fam}; see Generator attribution for the evidence ladder", "kind": "observed", "group": "technical", "refs": {"media": vs_[0]}}],
            "A generator family says how the media was made, not who made it.",
            alternatives=[("The signal was copied onto the file from another source.", "Check the evidence's reliability tier in Generator attribution.")],
            confirm=["provider"], disprove=["provider"], cautions=["This does not identify the person who ran the tool."]))

    # ---- roles per account -------------------------------------------------------------------------------------
    acct_out = []
    for key, plist in sorted(by_acct.items()):
        roles = []
        first_leads = [l for l in lead_list if l["type"] == "first_uploader" and key in l["subjects"]]
        for l in first_leads:
            roles.append({"role": "First known uploader", "confidence": l["confidence"], "basis": l["headline"]})
        for l in lead_list:
            if l["type"] == "altered_version" and key in l["subjects"]:
                roles.append({"role": "First known uploader of an altered version", "confidence": l["confidence"], "basis": l["evidence"][0]["text"]})
            if l["type"] == "closest_to_source" and key in l["subjects"]:
                roles.append({"role": "Posted the version closest to the source", "confidence": l["confidence"], "basis": l["headline"]})
        if not any(r["role"] == "First known uploader" for r in roles):
            ord_ = [p for p in plist if p["t_order"]]
            if ord_:
                roles.append({"role": "Distributor", "confidence": "moderate" if all(p["order_basis"] == "declared" for p in ord_) else "low",
                              "basis": "Posted after the media had already appeared at another account"})
        acct_out.append({"key": key, "label": plist[0]["account_label"], "platform": plist[0]["platform"], "kind": plist[0]["account_kind"],
                         "posts": [{"id": p["id"], "url": p["url"], "time": _fmt(p["t_order"]), "time_basis": p["time_basis"] or "capture time only",
                                    "versions": [vlabel.get(m) for m in p["media_ids"]], "state": p["state"], "caption": p["caption"][:140]} for p in sorted(plist, key=lambda p: p["t_order"] or datetime.max.replace(tzinfo=timezone.utc))],
                         "roles": roles, "not_established": ["Original creator", "Who operates the account", "Whether the account knows any other account here"]})

    # ---- versions out ------------------------------------------------------------------------------------------
    ver_out = []
    for v in ordered_versions:
        e = earliest(appear[v])
        par = parent_of.get(v)
        ver_out.append({"id": v, "label": vlabel[v], "thumb": thumbs.get(v), "width": media[v].width, "height": media[v].height, "size": media[v].size,
                        "mime": media[v].mime, "cluster": next((c["id"] for c in cluster_out if v in c["versions"]), None),
                        "parent": {"id": par["id"], "label": vlabel.get(par["id"]), "differences": par["differences"], "direction_status": "Inferred"} if par else None,
                        "appearances": [{"post": p["id"], "account": p["account_label"], "platform": p["platform"], "url": p["url"], "time": _fmt(p["t_order"]),
                                         "time_basis": p["time_basis"] or "capture time only"} for p in sorted(appear[v], key=lambda p: p["t_order"] or datetime.max.replace(tzinfo=timezone.utc))],
                        "earliest": {"post": e["id"], "account": e["account_label"], "time": _fmt(e["t_order"]), "basis": e["time_basis"] or "capture time only"} if e else None})

    # ---- graph: observed vs hypothesis -------------------------------------------------------------------------
    nodes, gedges = [], []
    for a in acct_out:
        nodes.append({"id": a["key"], "type": "account", "label": a["label"], "platform": a["platform"], "roles": [r["role"] for r in a["roles"]]})
    for v in ver_out:
        nodes.append({"id": v["id"], "type": "version", "label": v["label"], "thumb": v["thumb"]})
    for p in posts:
        for m in p["media_ids"]:
            gedges.append({"source": p["account"], "target": m, "kind": "observed", "status": "Captured", "label": "posted" + (f" · {_fmt(p['t_order'])[:16]}" if p["t_order"] else ""),
                           "evidence": f"We captured {p['url']} and it shows this file"})
    for e in vedges:
        if e["a"] in uf and e["b"] in uf:
            gedges.append({"source": e["a"], "target": e["b"], "kind": "observed" if e["verified"] else "hypothesis", "status": "Verified" if e["verified"] else "Lead",
                           "label": ("same content" if e["verified"] else "looks similar"), "evidence": "; ".join(e["differences"]) or "same picture content",
                           "direction": e["from"] if e["direction"] else None, "direction_status": "Inferred" if e["direction"] else None})
    for l in lead_list:
        if l["type"] == "link" and len(l["subjects"]) == 2:
            gedges.append({"source": l["subjects"][0], "target": l["subjects"][1], "kind": "hypothesis", "status": "Hypothesis", "label": f"possible link · {l['confidence']}",
                           "evidence": "; ".join(e["text"] for e in l["evidence"][:3]), "lead": l["id"]})
    verifies = [p for p in posts if p["order_basis"] == "declared"]
    lead_list.sort(key=lambda l: -l["rank"])
    for i, l in enumerate(lead_list, 1):
        l["priority"] = i
    n_acct = len(acct_out)
    return {
        "principle": PRINCIPLE, "roles_glossary": ROLE_GLOSSARY,
        "summary": {"accounts": n_acct, "posts": len(posts), "versions": len(ver_out), "families": len(cluster_out), "leads": len(lead_list),
                    "posts_with_declared_time": len(verifies),
                    "headline": (f"{n_acct} account(s) posted {len(ver_out)} version(s) of the media; {len(lead_list)} lead(s) generated."
                                 if posts else "No captured posts yet. Investigate a link first."),
                    "time_quality": ("All posting times are declared by the pages." if verifies and len(verifies) == len(posts) else
                                     "Some posts have no declared time; the order of those rests on when this app captured them, which is not when they were posted." if posts else "")},
        "accounts": acct_out, "versions": ver_out, "families": cluster_out, "leads": lead_list, "graph": {"nodes": nodes, "edges": gedges},
        "limits": ["Only public pages, seen as a logged-out visitor, were examined. Private, deleted and logged-in content is invisible.",
                   "Posting times are the times the pages declare (they can be edited, rounded or in another time zone).",
                   "All relationships between accounts are hypotheses until confirmed by authorised evidence.",
                   "Authorised evidence means records held by platforms or devices, obtainable only through lawful process by the police or a court."],
        "authorised_evidence": AUTHORISED,
    }


def _lead(kind: str, headline: str, subjects: list[str], confidence: str, value: float, evidence: list[dict], why: str, *,
          alternatives: list[tuple[str, str]], confirm: list[str], disprove: list[str], cautions: list[str], cluster: str | None = None,
          score: float | None = None) -> dict:
    cw = {"high": 1.0, "moderate": 0.7, "low": 0.4}[confidence]
    return {
        "id": _id(kind, *sorted(subjects), cluster or ""), "type": kind, "headline": sanitize_text(headline), "subjects": subjects,
        "confidence": confidence, "confidence_reason": sanitize_text(why), "rank": round(value * cw + (score or 0) * 0.1, 3), "cluster": cluster,
        "evidence": [{**e, "text": sanitize_text(e["text"])} for e in evidence],
        "observed_count": sum(1 for e in evidence if e["kind"] == "observed"), "hypothesis_count": sum(1 for e in evidence if e["kind"] == "hypothesis"),
        "alternatives": [{"explanation": a, "how_to_test": t} for a, t in alternatives],
        "would_confirm": [{"key": k, "text": AUTHORISED[k]} for k in confirm],
        "would_disprove": [{"key": k, "text": AUTHORISED[k]} for k in disprove],
        "cautions": cautions,
    }
