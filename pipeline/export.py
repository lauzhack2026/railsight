"""Write the web app's data: segments (GeoJSON), per-segment details, incidents, step-1 overlays and the summary."""
import json, math
from collections import defaultdict

import numpy as np
from PIL import Image

from common import APPDATA, LAYERS, ROOT, WORK, dump, mosaic, px_to_lonlat
from features import track_raster, tree_heights

MASKS = ROOT / "app" / "public" / "masks"
MASKS.mkdir(parents=True, exist_ok=True)
HCODE = {"tree": "t", "rock": "r", "slide": "s", "debris": "d", "flood": "f", "none": "n"}


def simplify(coords, tol_m=2.0):
    """Ramer-Douglas-Peucker in local metres."""
    pts = np.array(coords, float)
    if len(pts) < 3:
        return coords
    k = math.cos(math.radians(pts[:, 1].mean()))
    xy = np.c_[pts[:, 0] * 111320 * k, pts[:, 1] * 110574]
    keep = np.zeros(len(pts), bool); keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ab = xy[b] - xy[a]; n = np.hypot(*ab) or 1e-9
        v = xy[a + 1:b] - xy[a]
        d = np.abs(ab[0] * v[:, 1] - ab[1] * v[:, 0]) / n
        i = int(np.argmax(d))
        if d[i] > tol_m:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return [coords[i] for i in np.flatnonzero(keep)]


def make_mask(seg):
    """Step 1's view of the trees, as a map overlay: dark green = tall enough to reach the track, light = other trees."""
    parts = seg["open"]
    _, _, z, buf = LAYERS["vhm"]
    (x0, y0, x1, y1), dist, res, _ = track_raster(parts, z, buf)
    vh, _ = mosaic("vhm", z, x0, y0, x1, y1, "RGBA")
    lo, mid = tree_heights(vh)
    band = (dist >= 3) & (dist < 35)
    a = np.zeros(lo.shape + (4,), np.uint8)
    a[band & (lo >= 3)] = (150, 230, 120, 150)
    a[band & (lo >= 3) & (mid >= dist)] = (32, 190, 90, 210)   # mid = upper bound of the height class
    a[(dist >= 34) & (dist < 35.5)] = (255, 255, 255, 120)
    Image.fromarray(a, "RGBA").save(MASKS / f"{seg['id']}.png", optimize=True)
    lon0, lat1 = px_to_lonlat(x0, y0, z)
    lon1, lat0 = px_to_lonlat(x1, y1, z)
    return [round(lon0, 6), round(lat0, 6), round(lon1, 6), round(lat1, 6)]


def main(masks=True):
    segs = json.load(open(WORK / "segments.json"))
    feats = json.load(open(WORK / "features.json"))
    A = json.load(open(WORK / "assess.json"))
    trains = json.load(open(WORK / "trains_per_edge.json"))
    incs = json.load(open(WORK / "incidents_mapped.json"))
    segs = [s for s in segs if s["id"] in A]
    # incidents of any cause on scanned track (shown per section), index-addressed
    inc_out, seg_inc = [], defaultdict(list)
    for o in sorted(incs, key=lambda o: o["start"]):
        if not o["segments"]:
            continue
        idx = len(inc_out)
        inc_out.append({"id": str(idx), "date": o["start"][:10], "year": o["year"], "cls": o["cls"], "reason": o["reason_en"],
                        "section": o["section"], "dur": o["duration_min"]})
        for s in set(o["segments"]):
            seg_inc[s].append(idx)
    open_ids = [s["id"] for s in segs if A[s["id"]]["gate"] != "tunnel"]
    ranked = sorted(open_ids, key=lambda i: -A[i]["risk"])
    pos = {i: r / max(1, len(ranked)) for r, i in enumerate(ranked)}
    fc, det = [], {}
    n_mask = 0
    for s in segs:
        a = A[s["id"]]; f = feats[s["id"]]
        g = "T" if a["gate"] == "tunnel" else ("V" if "vision" in a else ("L" if a["gate"] == "text" else "Q"))
        rnd = lambda co: [[round(x, 5), round(y, 5)] for x, y in simplify(co)]
        if s["tunnel"] > 0.02:   # the whole segment, dashed: shows where the track runs underground
            fc.append({"type": "Feature", "properties": {"id": s["id"] + "~t", "r": 0, "h": "n", "g": "T"},
                       "geometry": {"type": "LineString", "coordinates": rnd(s["coords"])}})
        if g != "T":             # coloured: only the open parts
            parts = [rnd(p) for p in s["open"] if len(p) > 1]
            fc.append({"type": "Feature", "properties": {"id": s["id"], "r": int(a["risk"]), "h": HCODE.get(a.get("hazard", "none"), "n"), "g": g},
                       "geometry": {"type": "MultiLineString", "coordinates": parts}})
        d = {"from": s["from"], "to": s["to"], "k": s["k"], "n": s["n"], "op": s["op"], "len": s["len"], "tunnel": s["tunnel"],
             "trains": trains.get(s["edge"], 0), "pct": round(pos.get(s["id"], 1.0), 4), "f": f, "a": a, "inc": seg_inc.get(s["id"], [])}
        if masks and a["gate"] == "text" and (a["risk"] >= 35 or "vision" in a or seg_inc.get(s["id"])):
            try:
                d["mask"] = make_mask(s); n_mask += 1
            except FileNotFoundError:
                pass
        det[s["id"]] = d
    dump({"type": "FeatureCollection", "features": fc}, APPDATA / "segments.json")
    dump(det, APPDATA / "details.json")
    dump(inc_out, APPDATA / "incidents.json")

    # ---------------- summary: cascade, cost, latency, evaluation
    calls = [json.loads(l) for l in open(WORK / "calls.jsonl")] if (WORK / "calls.jsonl").exists() else []
    by = defaultdict(list)
    for c in calls:
        by[c["kind"]].append(c)
    T, V, N = by["text"], by["vision"], by["naive"]
    gates = defaultdict(int)
    for s in segs:
        gates[A[s["id"]]["gate"]] += 1
    n_vision = sum(1 for s in segs if "vision" in A[s["id"]])
    km = sum(s["len"] for s in segs) / 1000
    open_km = sum(s["len"] * (1 - s["tunnel"]) for s in segs) / 1000
    usd_text = sum(c["usd"] for c in T); usd_vision = sum(c["usd"] for c in V)
    seg_per_100km = 100 / (np.mean([s["len"] for s in segs]) / 1000)
    naive_per_seg = float(np.mean([c["usd"] for c in N])) if N else 0.0
    open_frac = open_km / km
    fs = [WORK / "fetch_stats_repair.json", WORK / "fetch_stats.json"]
    fetch = next((json.load(open(p)) for p in fs if p.exists()), {"tiles": 0, "bytes": 0, "seconds": 0})
    fstats = json.load(open(WORK / "features_stats.json")) if (WORK / "features_stats.json").exists() else {"ms_per_segment": 0}
    ev = json.load(open(WORK / "eval.json")) if (WORK / "eval.json").exists() else {"k": 0.1, "test_n": 0, "test_years": "2026", "rows": [], "all_n": 0, "all_rows": [], "tests": []}
    ev["note"] = NOTE
    summary = {
        "scan": {"segments": len(segs), "km": round(km, 1), "open_km": round(open_km, 1), "tunnel_km": round(km - open_km, 1),
                 "tiles": fetch["tiles"], "mb": round(fetch.get("bytes", 0) / 1e6, 1), "ms_per_segment": fstats["ms_per_segment"],
                 "minutes_fetch": round(fetch.get("seconds", 0) / 60, 1)},
        "cascade": {"tunnel": gates["tunnel"], "quiet": gates["quiet"], "text": gates["text"] + gates["failed"], "vision": n_vision,
                    "text_calls": len(T), "vision_calls": len(V), "km_text": 0, "km_vision": 0},
        "cost": {"model": "Claude Haiku 5.5", "naive_model": "Claude Opus 5.5",
                 "text_in": sum(c["in"] for c in T), "text_out": sum(c["out"] for c in T),
                 "vision_in": sum(c["in"] for c in V), "vision_out": sum(c["out"] for c in V),
                 "usd_text": round(usd_text, 4), "usd_vision": round(usd_vision, 4), "usd_total": round(usd_text + usd_vision, 4),
                 "usd_per_100km": round((usd_text + usd_vision) / km * 100, 5),
                 "naive_usd_per_100km": round(naive_per_seg * seg_per_100km, 4),
                 "naive_sample": len(N), "ratio": round((naive_per_seg * seg_per_100km) / max(1e-9, (usd_text + usd_vision) / km * 100), 1),
                 "text_s": round(float(np.mean([c["s"] for c in T])), 2) if T else 0, "vision_s": round(float(np.mean([c["s"] for c in V])), 2) if V else 0,
                 "naive_s": round(float(np.mean([c["s"] for c in N])), 2) if N else 0},
        "eval": ev,
        "top": ranked[:40],
    }
    dump(summary, APPDATA / "summary.json", indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("top", "eval")}, indent=1), "masks", n_mask)


NOTE = ("Natural-hazard incidents are rare: a few dozen in 31 months, so the intervals are wide and one incident more or "
        "less moves a share by several points. Incidents are only known per station-to-station section, not to the metre, "
        "and the liveticker lists the disruptions SBB announced to passengers, not every tree that fell. Railsight ranks "
        "exposure to screen the network cheaply and say where to look first; it is not a validated predictor.")

if __name__ == "__main__":
    main()
