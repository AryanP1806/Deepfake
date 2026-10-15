"""Graph analytics used to *prioritise leads* — never to assign responsibility."""
from __future__ import annotations

import networkx as nx


def analyse(G: nx.MultiDiGraph, genealogy: dict) -> dict:
    U = nx.Graph(G.to_undirected())
    comps = sorted(nx.connected_components(U), key=len, reverse=True)
    btw = nx.betweenness_centrality(U) if U.number_of_nodes() <= 800 else {}
    roots = {f["root"] for f in genealogy.get("families", [])}
    earliest = {f.get("earliest_observed") for f in genealogy.get("families", [])}

    accounts = []
    for n, d in G.nodes(data=True):
        if d.get("type") != "account" or d.get("merged_into"):
            continue  # overlay-only handles that match an observed account are folded into that account
        urls = [u for u, _, ed in G.in_edges(n, data=True) if ed.get("rel") == "posted_by"]
        media_posted = {m for url in urls for m, _, ed in G.in_edges(url, data=True) if ed.get("rel") == "found_at"}
        overlay = any(ed.get("rel") == "handle_visible_in_media" for _, _, ed in G.in_edges(n, data=True)) or \
            any(ed.get("rel") == "same_handle" for _, _, ed in G.in_edges(n, data=True))
        posted_root = bool(media_posted & roots)
        posted_earliest = bool(media_posted & earliest)
        published = sorted(filter(None, (G.nodes[u].get("published") for u in urls)))
        score = (2.0 * posted_root + 2.0 * posted_earliest + 1.5 * overlay + 0.5 * len(media_posted)
                 + 5 * btw.get(n, 0))
        reasons = []
        if posted_root:
            reasons.append("posted the inferred closest-to-source copy")
        if posted_earliest:
            reasons.append("posted the earliest observed copy")
        if overlay:
            reasons.append("its handle is burned into a copy of the media")
        if len(media_posted) > 1:
            reasons.append(f"posted {len(media_posted)} distinct copies")
        lookalikes = [G.nodes[o].get("label") for _, o, ed in G.out_edges(n, data=True) if ed.get("rel") == "similar_username"] +             [G.nodes[o].get("label") for o, _, ed in G.in_edges(n, data=True) if ed.get("rel") == "similar_username"]
        if lookalikes:
            reasons.append(f"look-alike handle of {', '.join(sorted(set(filter(None, lookalikes))))}")
            score += 0.5
        if btw.get(n, 0) > 0.05:
            reasons.append("bridges otherwise separate parts of the graph")
        accounts.append({"node": n, "label": d.get("label"), "platform": d.get("platform"), "score": round(score, 2),
                         "reasons": reasons, "urls": urls, "media": sorted(media_posted),
                         "first_published": published[0] if published else None})
    accounts.sort(key=lambda a: -a["score"])
    return {"components": [sorted(c) for c in comps], "centrality": {k: round(v, 4) for k, v in btw.items() if v > 0},
            "ranked_accounts": accounts}
