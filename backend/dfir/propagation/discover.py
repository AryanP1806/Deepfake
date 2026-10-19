"""Find other copies of the media across the public web — and decide which ones are really the same media.

Where candidates come from (each is only a *candidate* until its media is captured and verified):

  1. image-search APIs that are configured (SerpAPI Google Lens, TinEye) — they receive the image, so they need
     the case-scoped consent that the user ticked when starting the search;
  2. text search on the caption / page title / on-screen text: configured APIs (SerpAPI, YouTube, Reddit) and
     DuckDuckGo (no key; only the text is sent);
  3. link expansion: links on already-captured pages that point to other posts on known platforms, or to other
     pages of the same site.

A candidate becomes a *related copy* only if media captured from it is geometrically verified against the
case's media (same content: crop / mirror / re-encode ...). Similar-looking but unverified media is labelled
"similar", never "copy". Nothing here claims to see private chats, closed groups or logged-in content.

Manual reverse-image links (Google Lens, Bing, Yandex, TinEye) are generated for the user to open: the app
cannot read those result pages automatically.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote, urlparse

from sqlmodel import Session, select

from ..core import consent
from ..core.config import get_key
from ..core.db import session, vault_for
from ..core.models import Case, Location, MatchEdge, Media
from ..safety.gate import SafetyPolicy
from .adapters.search import ADAPTERS, get_adapter
from .intake import add_locations, normalise_url, run_adapter_search
from .platforms import classify

log = logging.getLogger("dfir.discover")

POST_PATTERNS = [re.compile(p, re.I) for p in (
    r"instagram\.com/(p|reel|reels|tv)/[\w-]+", r"(x|twitter)\.com/[^/]+/status/\d+",
    r"youtube\.com/(watch\?|shorts/)|youtu\.be/[\w-]+", r"tiktok\.com/@[^/]+/video/\d+",
    r"facebook\.com/.+/(posts|videos|photos)/|fb\.watch/|facebook\.com/(watch|reel)", r"t\.me/[A-Za-z0-9_]+/\d+",
    r"reddit\.com/r/[^/]+/comments/", r"threads\.(net|com)/@[^/]+/post/", r"sharechat\.com/post/")]
SKIP = re.compile(r"(login|signin|sign-in|signup|register|privacy|terms|about|contact|cookie|sharer|intent/|share\?|"
                  r"/tag/|/tags/|/category/|/feed|\.xml$|\.css$|\.js$|mailto:|javascript:|wa\.me|w3\.org|schema\.org)", re.I)
SAME_SITE_NAV = re.compile(r"^/?(#.*)?$")
SEARCH_CANDIDATES_PER_ROUND = 8  # public web search is noisy: capture only the top results of each source


def _reg_domain(host: str) -> str:
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "gov", "ac", "net"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ------------------------------------------------------------------------------------------------ manual links
def reverse_search_links(public_url: str | None) -> list[dict]:
    """Reverse-image search pages the USER opens. With a public image URL they open pre-filled; without one the
    user uploads the file on the site. Nothing is sent by the app."""
    u = quote(public_url, safe="") if public_url else None
    return [
        {"name": "Google Lens", "url": f"https://lens.google.com/uploadbyurl?url={u}" if u else "https://lens.google.com/",
         "prefilled": bool(u), "best_for": "Finds the same or similar images and pages that show them"},
        {"name": "Bing Visual Search", "url": f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{u}"
         if u else "https://www.bing.com/visualsearch", "prefilled": bool(u), "best_for": "Good for people and products"},
        {"name": "Yandex Images", "url": f"https://yandex.com/images/search?rpt=imageview&url={u}" if u else "https://yandex.com/images/",
         "prefilled": bool(u), "best_for": "Often best at finding faces and social-media copies"},
        {"name": "TinEye", "url": f"https://tineye.com/search?url={u}" if u else "https://tineye.com/",
         "prefilled": bool(u), "best_for": "Finds exact and edited copies; shows the oldest match it knows"},
    ]


def public_media_url(case_id: str, m: Media) -> str | None:
    rec = vault_for(case_id).record(m.sha256) or {}
    acq = rec.get("acquisition") or {}
    url = acq.get("media_url")
    return url if url and urlparse(url).scheme in ("http", "https") and not _is_private(url) else None


def _is_private(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local") or host.startswith(("10.", "192.168."))


# ------------------------------------------------------------------------------------------------ relations
def relations(s: Session, case_id: str) -> dict[str, dict]:
    """How each location relates to the case's primary media, computed from the verified match graph."""
    media = s.exec(select(Media).where(Media.case_id == case_id)).all()
    locs = s.exec(select(Location).where(Location.case_id == case_id)).all()
    edges = s.exec(select(MatchEdge).where(MatchEdge.case_id == case_id)).all()
    by_loc: dict[str, list[Media]] = {l.id: [] for l in locs}
    for m in media:
        for l in locs:
            if m.id in (l.media_ids or []) or m.location_id == l.id:
                by_loc[l.id].append(m)
    verified_adj: dict[str, set[str]] = {}
    any_adj: dict[str, set[str]] = {}
    for e in edges:
        for a, b in ((e.a_media, e.b_media), (e.b_media, e.a_media)):
            any_adj.setdefault(a, set()).add(b)
            if e.verified:
                verified_adj.setdefault(a, set()).add(b)
    roots = [m.id for m in media if m.role == "primary"] or [m.id for m in media if m.location_id in
                                                             {l.id for l in locs if l.seed}]
    comp, stack = set(roots), list(roots)
    while stack:
        for n in verified_adj.get(stack.pop(), ()):
            if n not in comp:
                comp.add(n)
                stack.append(n)
    out: dict[str, dict] = {}
    for l in locs:
        ms = by_loc[l.id]
        ids = {m.id for m in ms}
        if l.dismissed:
            st, why = "dismissed", "You marked this as unrelated."
        elif l.seed:
            st, why = "seed", "The link you started from."
        elif ids & comp:
            st, why = "verified_copy", "Media from this page was verified as the same content as the suspect media."
        elif any(any_adj.get(i, set()) & comp for i in ids):
            st, why = "similar", "Looks similar, but the match could not be verified geometrically."
        elif ms:
            st, why = "no_match", "Media was captured but does not match the suspect media."
        elif l.state in ("restricted", "blocked"):
            st, why = "no_media", "The page needs a login, so its media could not be read."
        elif l.state in ("removed", "error"):
            st, why = "no_media", "The page was unavailable when captured."
        elif l.state == "pending":
            st, why = "pending", "Not captured yet."
        else:
            st, why = "no_media", "No downloadable image or video was found on the page."
        out[l.id] = {"status": st, "reason": why, "media_ids": sorted(ids)}
    return out


# ------------------------------------------------------------------------------------------------ candidates
def page_link_candidates(loc: Location, limit: int = 8) -> list[tuple[str, str]]:
    """(url, why) for links on a captured page that may hold another copy."""
    links = (loc.page_meta or {}).get("links") or []
    if not links:
        return []
    page_dom = _reg_domain(urlparse(loc.final_url or loc.url).hostname or "")
    me = {loc.url.rstrip("/"), (loc.final_url or "").rstrip("/")}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    is_platform = classify(loc.url)["platform"] not in ("", "Website", "Local/test site")
    for l in links:
        href = l.get("href", "")
        key = href.split("#")[0].rstrip("/")
        if not href or key in me or key in seen or SKIP.search(href):
            continue
        host = urlparse(href).hostname or ""
        post = any(p.search(href) for p in POST_PATTERNS)
        same_site = _reg_domain(host) == page_dom and not is_platform and not SAME_SITE_NAV.match(urlparse(href).path or "")
        if post or same_site:
            seen.add(key)
            out.append((href.split("#")[0], "link to another post on " + (classify(href)["platform"] or host)
                        if post else "another page on the same site"))
        if len(out) >= limit:
            break
    return out


def text_queries(s: Session, case_id: str) -> list[str]:
    """Phrases that a re-post of this media would likely share: caption, page title, on-screen text."""
    qs: list[str] = []
    for l in s.exec(select(Location).where(Location.case_id == case_id, Location.seed == True)).all():  # noqa: E712
        for t in (l.caption, (l.page_meta or {}).get("og:title"), (l.page_meta or {}).get("title")):
            t = re.sub(r"\s+", " ", (t or "")).strip()
            if len(t) >= 25 and t not in qs:
                qs.append(t[:110])
    for m in s.exec(select(Media).where(Media.case_id == case_id, Media.role == "primary")).all():
        lines = sorted(((m.fingerprints or {}).get("ocr") or []), key=len, reverse=True)
        for t in lines[:1]:
            if len(t) >= 25 and t not in qs:
                qs.append(t[:110])
    return qs[:2]


# ------------------------------------------------------------------------------------------------ the search
def _tag(case_id: str, ids: list[str], depth: int, found_from: str) -> None:
    with session() as s:
        for lid in ids:
            l = s.get(Location, lid)
            if l:
                l.depth, l.found_from = depth, found_from
                s.add(l)
        s.commit()


def search_round(case_id: str, depth: int, *, remaining: int, sources: list[dict], crawl_from: list[str] | None = None) -> list[str]:
    """One discovery round. Returns ids of NEW candidate locations (not yet captured). Records every source's
    outcome in ``sources`` so the UI can state exactly what was and was not searched."""
    new_ids: list[str] = []

    def note(name: str, status: str, detail: str, found: int = 0) -> None:
        sources.append({"name": name, "status": status, "detail": detail, "found": found, "round": depth})

    with session() as s:
        case = s.get(Case, case_id)
        policy = SafetyPolicy.from_case(case.safety)
        prim = s.exec(select(Media).where(Media.case_id == case_id, Media.role == "primary")).all()
        queries = text_queries(s, case_id) if depth == 1 else []
        crawl_locs = [s.get(Location, i) for i in (crawl_from or [])]
        if depth == 1:
            crawl_locs = s.exec(select(Location).where(Location.case_id == case_id, Location.seed == True)).all()  # noqa: E712
        can_send = {a.name: consent.has(s, a.consent_action, case_id) for a in ADAPTERS if a.consent_action}
    if policy.minors_possible:
        note("all searches", "skipped", "Possible minor involved: nothing is downloaded or sent anywhere.")
        return []

    # 1 + 2: adapters ------------------------------------------------------------------------------------
    if depth == 1:
        for a in ADAPTERS:
            if a.kind not in ("image", "text") or a.name == "bing_visual":
                continue
            nr = a.available()
            if nr:
                note(a.title, "skipped", nr.reason)
                continue
            if a.consent_action and not can_send.get(a.name):
                note(a.title, "skipped", "Needs your permission to send the image/text — tick 'search the web' to allow.")
                continue
            try:
                found: list[str] = []
                if a.kind == "image":
                    if not prim:
                        note(a.title, "skipped", "No suspect media to search with yet.")
                        continue
                    for m in prim[:1]:
                        r = run_adapter_search(case_id, a.name, media_id=m.id, limit=SEARCH_CANDIDATES_PER_ROUND)
                        if r.get("not_run"):
                            note(a.title, "skipped", r["not_run"].get("reason", "not available"))
                        else:
                            found += r["new_locations"]
                else:
                    if not queries:
                        note(a.title, "skipped", "No caption, title or on-screen text to search for.")
                        continue
                    for q in queries:
                        found += run_adapter_search(case_id, a.name, query=q, limit=max(3, SEARCH_CANDIDATES_PER_ROUND // len(queries))).get("new_locations", [])
                _tag(case_id, found, depth, a.name)
                if found or not any(x["name"] == a.title and x["round"] == depth for x in sources):
                    note(a.title, "ran", f"{len(found)} new candidate page(s)", len(found))
                new_ids += found
            except Exception as e:  # noqa: BLE001 — a failing provider must not stop the investigation
                note(a.title, "failed", f"{type(e).__name__}: {str(e)[:160]}")
        if not any(x["name"].startswith(("Google Lens", "TinEye")) and x["status"] == "ran" for x in sources):
            note("Automatic reverse-image search", "unavailable",
                 "No image-search API key is configured (SerpAPI / TinEye). Use the 'Search by image' links to look "
                 "yourself, and paste any pages you find.")

    # 3: link expansion ----------------------------------------------------------------------------------
    cands: list[tuple[str, str, str]] = []
    for l in crawl_locs:
        if l:
            cands += [(u, why, l.id) for u, why in page_link_candidates(l)]
    if cands:
        with session() as s:
            locs = add_locations(s, case_id, [{"url": u, "notes": f"Found as: {why}", "depth": depth, "found_from": f}
                                              for u, why, f in cands[:remaining]], discovered_via="page_links")
            ids = [l.id for l in locs]
        new_ids += ids
        note("Links on captured pages", "ran", f"{len(ids)} new page(s) linked from captured pages", len(ids))
    else:
        note("Links on captured pages", "ran", "No links to other posts or pages were found.", 0)
    return new_ids[:remaining]


def grant_search_consent(case_id: str) -> list[str]:
    """The user ticked 'search the web for copies': record case-scoped consent for the sending actions."""
    granted = []
    with session() as s:
        for action in ("search:serpapi", "search:tineye", "search:duckduckgo"):
            if not consent.has(s, action, case_id):
                consent.grant(s, action, case_id, scope="case", detail={"via": "start-investigation checkbox"})
                granted.append(action)
    if granted:
        vault_for(case_id).custody.append("consent.granted", actor="investigator",
                                          details={"actions": granted, "via": "start-investigation checkbox"})
    return granted


def configured_providers() -> dict:
    return {"image": [a.title for a in ADAPTERS if a.kind == "image" and a.available() is None],
            "text": [a.title for a in ADAPTERS if a.kind == "text" and a.available() is None],
            "has_keys": bool(get_key("SERPAPI_KEY") or get_key("TINEYE_API_KEY"))}


__all__ = ["relations", "reverse_search_links", "public_media_url", "search_round", "grant_search_consent",
           "page_link_candidates", "text_queries", "configured_providers", "get_adapter"]
