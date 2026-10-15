"""Build the media family tree from pairwise match edges.

Each matched pair contributes candidate directed edges (ancestor → descendant). Direction
evidence decides *orientation*; the edge weight prefers the *closest* relative (high overlap,
few transformations), so a copy attaches to its nearest ancestor rather than a distant one.
A maximum spanning arborescence (Edmonds) per connected family gives the derivation tree.
The root is the *inferred closest-to-source copy among those discovered*. It is never
asserted to be the true original.
"""
from __future__ import annotations

import networkx as nx

DIRECTION_DECISIVE = 1.0


def _closeness(e: dict, transforms: list) -> float:
    g = e.get("geometry") or {}
    overlap = (g.get("cov_a", 0.5) + g.get("cov_b", 0.5)) / 2 if g else 0.5
    inl = g.get("inlier_ratio", 0.3) if g else 0.3
    return (10.0 if e.get("verified") else 5.0) + 4.0 * overlap + 2.0 * inl - 0.7 * len(transforms)


def build_tree(nodes: list[dict], edges: list[dict]) -> dict:
    """nodes: [{id, time}]; edges: [{id, a, b, direction_score, verified, tier, geometry,
    transforms_b_from_a, transforms_a_from_b, notes}]"""
    G = nx.DiGraph()
    G.add_nodes_from(n["id"] for n in nodes)
    U = nx.Graph()
    U.add_nodes_from(G.nodes)
    for e in edges:
        U.add_edge(e["a"], e["b"])
        ds = e.get("direction_score", 0.0)
        bonus = 0.5 * min(abs(ds), 3.0)
        if ds > -DIRECTION_DECISIVE:
            G.add_edge(e["a"], e["b"], weight=_closeness(e, e["transforms_b_from_a"]) + (bonus if ds > 0 else 0),
                       match=e, forward=True)
        if ds < DIRECTION_DECISIVE:
            G.add_edge(e["b"], e["a"], weight=_closeness(e, e["transforms_a_from_b"]) + (bonus if ds < 0 else 0),
                       match=e, forward=False)

    times = {n["id"]: n.get("time") for n in nodes}
    families, tree_edges = [], []
    for comp in nx.connected_components(U):
        comp = list(comp)
        timed = [(times[n], n) for n in comp if times.get(n)]
        earliest = min(timed)[1] if timed else None
        if len(comp) == 1:
            families.append({"members": comp, "root": comp[0], "earliest_observed": earliest})
            continue
        sub = G.subgraph(comp).copy()
        try:
            arb = nx.maximum_spanning_arborescence(sub, attr="weight", preserve_attrs=True)
        except nx.NetworkXException:
            # decisive directions made a spanning tree impossible; relax to both directions
            sub = nx.DiGraph()
            for u, v, d in G.subgraph(comp).edges(data=True):
                sub.add_edge(u, v, **d)
                if not sub.has_edge(v, u):
                    sub.add_edge(v, u, **{**d, "forward": not d["forward"], "weight": d["weight"] - 3})
            arb = nx.maximum_spanning_arborescence(sub, attr="weight", preserve_attrs=True)
        root = next(n for n in arb.nodes if arb.in_degree(n) == 0)
        for u, v, d in arb.edges(data=True):
            m = d["match"]
            transforms = m["transforms_b_from_a"] if d["forward"] else m["transforms_a_from_b"]
            margin = abs(m.get("direction_score", 0))
            tree_edges.append({
                "parent": u, "child": v, "transforms": transforms, "verified_match": m.get("verified", False),
                "match_tier": m.get("tier"), "direction_confidence": "Inferred" if margin >= 1.5 else "Lead",
                "direction_margin": margin, "notes": m.get("notes", []), "match_id": m.get("id"),
            })
        families.append({"members": comp, "root": root, "earliest_observed": earliest})
    return {"families": families, "edges": tree_edges}
