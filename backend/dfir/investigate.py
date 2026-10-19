"""URL-to-investigation pipeline: paste a link → capture → extract media → analyse → find copies → compare → preserve.

One background job. Its progress is written to ``investigation.json`` in the case folder after every step, so the
UI can show exactly which stage is running and what each stage found — and what it could NOT do.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from sqlmodel import select

from .analyzers.base import registry
from .core.db import case_dir, session, vault_for
from .core.models import Case, Location, Media
from .genealogy.fingerprint import fingerprint_media
from .pipeline import analyze_media, run_genealogy
from .propagation import discover
from .propagation.capture import run_capture
from .propagation.intake import enrich
from .safety.gate import SafetyPolicy

log = logging.getLogger("dfir.investigate")

CANDIDATE_WORKERS = 3  # candidate pages are independent: check a few at once

STAGES = [
    ("capture", "Open the link and download its media"),
    ("analyse", "Check the media for AI generation or editing"),
    ("records", "Look up archive and website records"),
    ("discover", "Search for copies elsewhere"),
    ("compare", "Compare the copies and map the spread"),
    ("done", "Ready to review"),
]


class State:
    def __init__(self, case_id: str, options: dict):
        self.path = case_dir(case_id) / "investigation.json"
        self.d = {"started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "running": True,
                  "options": options, "stages": [{"key": k, "title": t, "status": "pending", "detail": ""} for k, t in STAGES],
                  "sources": [], "summary": None, "error": None}
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.d, indent=2, default=str), encoding="utf-8")

    def stage(self, key: str, status: str, detail: str = "") -> None:
        for st in self.d["stages"]:
            if st["key"] == key:
                st["status"], st["detail"] = status, detail or st["detail"]
        self.save()


def load_state(case_id: str) -> dict | None:
    p = case_dir(case_id) / "investigation.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _local_analyzers() -> set[str]:
    """Everything except outside services — copies are analysed locally so each copy doesn't spend Hive credit."""
    # copies skip the (slower) forensic modules; those are run per file on demand
    return {a.name for a in registry() if not a.name.startswith("api_") and a.name != "forensic_suite"}


def run_investigation(case_id: str, *, depth: int = 1, find_copies: bool = True, max_total: int = 20,
                      progress=lambda f, m: None) -> dict:
    with session() as s:
        case = s.get(Case, case_id)
        policy = SafetyPolicy.from_case(case.safety)
    st = State(case_id, {"depth": depth, "find_copies": find_copies, "max_total": max_total})
    v = vault_for(case_id)
    v.custody.append("investigation.start", actor="investigator", details={"depth": depth, "find_copies": find_copies})
    t0 = time.time()
    try:
        # 1. capture --------------------------------------------------------------------------------------------
        st.stage("capture", "running", "Opening the link…")
        progress(0.03, "Opening the link")
        new_media: list[str] = []
        with session() as s:
            pend = [l.id for l in s.exec(select(Location).where(Location.case_id == case_id, Location.state == "pending",
                                                                Location.depth == 0)).all()]
        notes = []
        for i, lid in enumerate(pend):
            with session() as s:
                loc = s.get(Location, lid)
                st.stage("capture", "running", f"Capturing {loc.url[:70]}")
                progress(0.03 + 0.2 * i / max(1, len(pend)), f"Capturing {loc.url[:50]}")
                r = run_capture(s, case_id, loc, analyse=False)
                new_media += r.get("media", [])
                s.refresh(loc)
                if r.get("state") not in ("live", "changed"):
                    notes.append(f"{loc.url[:50]}: {r.get('state')}" + (f" ({r['error'][:60]})" if r.get("error") else ""))
                elif r.get("video_error"):
                    notes.append(r["video_error"])
                elif not r.get("media"):
                    notes.append(f"{loc.url[:50]}: page saved, but no image or video could be downloaded from it")
        if policy.minors_possible:
            st.stage("capture", "done", "Page details recorded. Possible minor: media was NOT downloaded.")
        else:
            st.stage("capture", "done" if new_media or not pend else "error",
                     f"{len(new_media)} media file(s) downloaded" + ("; " + "; ".join(notes) if notes else ""))

        # 2. analyse -------------------------------------------------------------------------------------------
        with session() as s:
            todo = [m for m in s.exec(select(Media).where(Media.case_id == case_id)).all() if not m.fingerprints]
        if not todo:
            st.stage("analyse", "skipped" if not new_media else "done", "Nothing new to analyse")
        for i, m in enumerate(todo):
            st.stage("analyse", "running", f"Analysing {i + 1} of {len(todo)}: {m.label[:50]}")
            analyze_media(case_id, m.id, lambda f, msg, i=i: progress(0.25 + 0.3 * (i + f) / max(1, len(todo)), msg))
        if todo:
            st.stage("analyse", "done", f"{len(todo)} media file(s) analysed")

        # 3. archive & domain records ---------------------------------------------------------------------------
        st.stage("records", "running", "Wayback Machine and website registration records…")
        progress(0.58, "Archive and domain records")
        try:
            enrich(case_id)
            st.stage("records", "done", "Archive snapshots and website records saved")
        except Exception as e:  # noqa: BLE001
            st.stage("records", "error", f"Could not complete: {type(e).__name__}")

        # 4 + 5. discover copies, verify them -------------------------------------------------------------------
        if policy.minors_possible:
            st.stage("discover", "skipped", "Possible minor: nothing is searched or downloaded.")
        else:
            # Following links on captured pages sends nothing anywhere, so it always runs. Search services that
            # receive the image or caption run only with the user's permission (never auto-granted for intimate cases).
            if find_copies and not policy.intimate:
                discover.grant_search_consent(case_id)
            crawl_from: list[str] | None = None
            analysed_copies = 0
            with session() as s:
                analysed_ids = {m.id for m in s.exec(select(Media).where(Media.case_id == case_id)).all()
                                if m.role == "primary" or (m.fingerprints or {}).get("ocr") is not None}
            for rnd in range(1, depth + 1):
                with session() as s:
                    total = len(s.exec(select(Location).where(Location.case_id == case_id)).all())
                remaining = max_total - total
                if remaining <= 0:
                    break
                st.stage("discover", "running", f"Round {rnd} of {depth}: looking for copies…")
                progress(0.6 + 0.3 * (rnd - 1) / depth, f"Looking for copies (round {rnd})")
                ids = discover.search_round(case_id, rnd, remaining=remaining, sources=st.d["sources"], crawl_from=crawl_from)
                st.save()
                if not ids:
                    break
                got: list[str] = []
                got_media: list[str] = []
                def _check(lid: str) -> tuple[str, dict]:
                    """Open one candidate page (own browser + DB session) and fingerprint any media it shows."""
                    with session() as s2:
                        loc = s2.get(Location, lid)
                        r = run_capture(s2, case_id, loc, analyse=False, light=True)
                        for mid in r.get("media", []):
                            mm = s2.get(Media, mid)
                            if mm and not mm.fingerprints:
                                try:
                                    mm.fingerprints = fingerprint_media(vault_for(case_id).path(mm.sha256), mm.modality)
                                    s2.add(mm)
                                    s2.commit()
                                except Exception:  # noqa: BLE001
                                    pass
                    return lid, r

                done_n = 0
                with ThreadPoolExecutor(max_workers=CANDIDATE_WORKERS, thread_name_prefix="dfir-cand") as ex:
                    futs = [ex.submit(_check, lid) for lid in ids]
                    for fut in as_completed(futs):
                        done_n += 1
                        try:
                            lid, r = fut.result()
                        except Exception as e:  # noqa: BLE001 — one bad page must not stop the rest
                            log.warning("candidate capture failed: %s", e)
                        else:
                            got.append(lid)
                            got_media += r.get("media", [])
                        st.stage("discover", "running", f"Round {rnd}: checked {done_n} of {len(ids)} candidate pages")
                        progress(0.6 + 0.3 * (rnd - 1 + done_n / len(ids)) / depth, f"Checked {done_n}/{len(ids)} candidates")
                st.stage("compare", "running", "Comparing candidate media with the suspect media…")
                run_genealogy(case_id, new_media_ids=got_media)
                with session() as s:
                    rel = discover.relations(s, case_id)
                good = [lid for lid in got if rel.get(lid, {}).get("status") in ("verified_copy", "similar")]
                # Candidates that did not verify stay out of the case record (complaint, timeline, graph) but remain
                # listed under "checked, not included" so the user can see why and include one by hand.
                with session() as s:
                    for lid in ids:
                        if lid not in got:  # the capture itself crashed: nothing was saved, so nothing to keep
                            l = s.get(Location, lid)
                            l.dismissed, l.state = True, "error"
                            l.notes = (l.notes + "\nNot included automatically: the page could not be captured").strip()
                            s.add(l)
                    for lid in got:
                        r = rel.get(lid, {})
                        if r.get("status") != "verified_copy":
                            l = s.get(Location, lid)
                            l.dismissed = True
                            l.notes = (l.notes + "\nNot included automatically: " + r.get("reason", "no match")).strip()
                            s.add(l)
                    s.commit()
                for lid in [x for x in got if rel.get(x, {}).get("status") == "verified_copy"]:
                    st.stage("discover", "running", "Saving full evidence (screenshot, page archive) of the verified copies…")
                    with session() as s:
                        l = s.get(Location, lid)
                        try:
                            run_capture(s, case_id, l, analyse=False)
                            s.refresh(l)
                            if l.state == "changed":  # an immediate re-capture is not a real change of the page
                                l.state = "live"
                                s.add(l)
                                s.commit()
                        except Exception as e:  # noqa: BLE001
                            log.warning("full capture of a verified copy failed: %s", e)
                for lid in good:  # fully analyse only media that really matches (locally, without spending Hive credit)
                    for mid in rel[lid]["media_ids"]:
                        if mid in analysed_ids:  # already analysed (e.g. the page re-served the suspect file itself)
                            continue
                        analysed_ids.add(mid)
                        try:
                            analyze_media(case_id, mid, only=_local_analyzers())
                            analysed_copies += 1
                        except Exception as e:  # noqa: BLE001
                            log.warning("copy analysis failed: %s", e)
                crawl_from = good
                n_ver = sum(1 for lid in got if rel.get(lid, {}).get("status") == "verified_copy")
                st.stage("discover", "running", f"Round {rnd}: {len(got)} candidate page(s) checked, {n_ver} verified copy/copies")
                if not good:
                    break
            st.stage("discover", "done", f"{len([x for x in st.d['sources'] if x['status'] == 'ran'])} source(s) searched; "
                                         f"{analysed_copies} copy media analysed")

        # 6. compare + summary ---------------------------------------------------------------------------------
        st.stage("compare", "running", "Building the copy map…")
        progress(0.93, "Comparing media")
        with session() as s:
            n_fp = len([m for m in s.exec(select(Media).where(Media.case_id == case_id)).all() if m.fingerprints])
        # small cases: compare everything; larger ones: only the files this run analysed (links the user just added)
        run_genealogy(case_id) if n_fp <= 6 else run_genealogy(case_id, new_media_ids=[m.id for m in todo])
        with session() as s:
            rel = discover.relations(s, case_id)
            locs = s.exec(select(Location).where(Location.case_id == case_id)).all()
        counts: dict[str, int] = {}
        for r in rel.values():
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        st.d["summary"] = {"locations": len(locs), "by_relation": counts,
                           "verified_copies": counts.get("verified_copy", 0),
                           "platforms": sorted({l.platform or l.domain for l in locs}),
                           "seconds": round(time.time() - t0)}
        st.stage("compare", "done", f"{counts.get('verified_copy', 0)} verified cop{'y' if counts.get('verified_copy', 0) == 1 else 'ies'} "
                                    f"in {len(locs)} location(s)")
        st.stage("done", "done", "Review the result")
        progress(1.0, "Investigation complete")
    except Exception as e:  # noqa: BLE001
        log.exception("investigation failed")
        st.d["error"] = f"{type(e).__name__}: {e}"
        for stg in st.d["stages"]:
            if stg["status"] == "running":
                stg["status"], stg["detail"] = "error", st.d["error"][:200]
        raise
    finally:
        st.d["running"] = False
        st.d["finished_at"] = datetime.now(timezone.utc).isoformat()
        st.save()
        v.custody.append("investigation.finish", actor="system", details={"summary": st.d.get("summary"), "error": st.d.get("error")})
    return st.d
