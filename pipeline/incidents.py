"""Map SBB liveticker incidents (2024-03 .. 2026-10) onto the scanned segments, and count trains per edge."""
import gzip, json, re
from collections import defaultdict

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from common import INCIDENT_TEXT, INCIDENTS, NETWORK, WORK, dump

# Natural-hazard causes (EG codes of the liveticker's reason table) -> hazard class
CODE_CLASS = {
    "EG_025": "tree", "EG_026": "tree", "EG_101": "tree", "EG_104": "tree",        # fallen tree, storm damage, storms, wind
    "EG_017": "slope", "EG_018": "slope", "EG_112": "slope", "EG_113": "slope",    # landslide, rockslide, falling rocks
    "EG_021": "slope", "EG_022": "slope",                                          # avalanche
    "EG_019": "water", "EG_020": "water", "EG_198": "water", "EG_195": "water",    # flooding, heavy rain
    "EG_100": "obstacle",                                                          # obstacle on the tracks
}
KEYWORDS = [  # incidents filed under another cause whose texts name a natural hazard
    ("tree", re.compile(r"\b(trees?|tree branch|branch(es)? on|Baum|Bäume|arbres?|albero|alberi)\b", re.I)),
    ("obstacle", re.compile(r"\bobstacle on the tracks?\b", re.I)),
    ("slope", re.compile(r"\b(landslide|mudslide|rockfall|rock fall|falling rocks|Murgang|Erdrutsch|Steinschlag|glissement de terrain|éboulement|frana)\b", re.I)),
    ("water", re.compile(r"\b(flooding|flooded|Hochwasser|inondation|water on the track)\b", re.I)),
]
EXCLUDE = {"EG_110", "EG_039", "EG_192"}   # fires: trees mentioned, but not a lineside-hazard cause


def main():
    net = json.load(gzip.open(NETWORK))
    nodes, edges = net["nodes"], net["edges"]
    nid = {n[0]: i for i, n in enumerate(nodes)}
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    byname = {}
    for i, n in enumerate(nodes):
        byname.setdefault(norm(n[1]), i)
    # graph for shortest paths between named stations
    rows, cols, w = [], [], []
    for j, e in enumerate(edges):
        rows += [e[1], e[2]]; cols += [e[2], e[1]]; w += [e[4], e[4]]
    G = csr_matrix((w, (rows, cols)), shape=(len(nodes), len(nodes)))
    eidx = {}
    for j, e in enumerate(edges):
        eidx[(e[1], e[2])] = j; eidx[(e[2], e[1])] = j

    segs = json.load(open(WORK / "segments.json"))
    by_edge = defaultdict(list)
    for s in segs:
        by_edge[s["edge"]].append(s["id"])

    text = {}
    for o in json.load(open(INCIDENT_TEXT)):
        ids = None
        for t in o.get("timeline", []):
            f = t.get("facts") or {}
            a, b = (f.get("from_station") or {}).get("id"), (f.get("to_station") or {}).get("id")
            if a and b and ids is None:
                ids = (a, b)
        text[o["id"]] = (" ".join((t.get("title") or "") + " " + (t.get("text") or "") for t in o.get("timeline", [])), ids)

    out, unmatched = [], 0
    for inc in json.load(open(INCIDENTS)):
        cls = CODE_CLASS.get(inc["reason"])
        txt, ids = text.get(inc["id"], ("", None))
        src = "code"
        if cls is None and inc["reason"] not in EXCLUDE:
            for c, rx in KEYWORDS:
                if rx.search(txt):
                    cls, src = c, "text"; break
        # stations of the section
        pair = None
        if ids and ids[0] in nid and ids[1] in nid:
            pair = (nid[ids[0]], nid[ids[1]])
        elif inc["sections"]:
            a, b = (inc["sections"][0] + [None])[:2]
            if a and b and norm(a) in byname and norm(b) in byname:
                pair = (byname[norm(a)], byname[norm(b)])
        seg_ids = []
        if pair:
            dist, pred = dijkstra(G, indices=pair[0], return_predecessors=True)
            if np.isfinite(dist[pair[1]]) and dist[pair[1]] < 80000:
                v = pair[1]
                while v != pair[0]:
                    u = pred[v]
                    seg_ids += by_edge.get(edges[eidx[(u, v)]][0], [])
                    v = u
        if not seg_ids:
            unmatched += 1
        out.append({
            "id": inc["id"], "reason": inc["reason"], "reason_en": inc["reason_en"], "cls": cls, "src": src,
            "start": inc["start"], "year": int(inc["start"][:4]), "section": inc["section_text"],
            "effect": inc["effect"], "duration_min": inc["duration_min"], "segments": seg_ids,
        })
    dump(out, WORK / "incidents_mapped.json")
    nat = [o for o in out if o["cls"]]
    print(f"{len(out)} incidents, {unmatched} not on scanned track; natural hazard: {len(nat)}, "
          f"on scanned track: {sum(1 for o in nat if o['segments'])}")
    from collections import Counter
    print(Counter((o["year"], o["cls"]) for o in nat if o["segments"]))

    # trains per day per edge from one weekday of the twin's timetable
    paths = json.load(gzip.open(NETWORK.parent / "paths.json.gz"))
    day = json.load(gzip.open(NETWORK.parent / "days" / "day-2026-11-20.json.gz"))
    stops = day["stops"]
    cnt = defaultdict(int)
    for trip in day["trips"]:
        seq = trip[4][0::3]
        for a, b in zip(seq, seq[1:]):
            p = paths.get(f"{stops[a][0]}>{stops[b][0]}")
            if p:
                for i in p[2]:
                    cnt[edges[abs(i) - 1][0]] += 1
    dump(dict(cnt), WORK / "trains_per_edge.json")
    print("edges with traffic", len(cnt))


if __name__ == "__main__":
    main()
