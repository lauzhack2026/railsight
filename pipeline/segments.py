"""Cut the federal (BAV) rail network into ~1 km segments and mark the parts in tunnels (OSM)."""
import gzip, json, math, sqlite3
from collections import defaultdict

import numpy as np

from common import CACHE, GPKG, NETWORK, WORK, dump

OPERATORS = {"SBB CFF FFS", "BLS", "SOB", "TPF", "DICH", "SZU", "TMR", "ETB", "TRAVYS", "TRN", "CJ", "DB",
             "ST", "OeBB", "ÖBB-I", "SNCF", "AB", "SEHR", "VVT", "AVA"}
TARGET_M = 1000.0
TUNNEL_TOL_M = 30.0


def local_xy(lon, lat, lat0):
    return np.c_[(np.asarray(lon) * 111320.0 * math.cos(math.radians(lat0))), np.asarray(lat) * 110574.0]


def cut(pts, n):
    """Split a polyline (N,2 lon/lat) into n pieces of equal length."""
    lat0 = float(np.mean(pts[:, 1]))
    xy = local_xy(pts[:, 0], pts[:, 1], lat0)
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    L = d[-1]
    pieces = []
    for k in range(n):
        a, b = L * k / n, L * (k + 1) / n
        inner = [i for i in range(len(d)) if a < d[i] < b]
        def at(s):
            i = min(max(np.searchsorted(d, s) - 1, 0), len(d) - 2)
            t = 0 if d[i + 1] == d[i] else (s - d[i]) / (d[i + 1] - d[i])
            return pts[i] + t * (pts[i + 1] - pts[i])
        seg = [at(a)] + [pts[i] for i in inner] + [at(b)]
        pieces.append((np.array(seg), b - a))
    return pieces


def main():
    net = json.load(gzip.open(NETWORK))
    nodes = net["nodes"]
    con = sqlite3.connect(GPKG)
    info = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "select interlis_oid, Infrastrukturbetr_TUAbkuerzung, Spurweite, Elektrifizierung from Netzsegment")}

    # OSM rail tunnels -> grid index of line pieces in local metres
    osm = json.load(open(CACHE / "osm_tunnels.json"))["elements"]
    LAT0 = 46.8
    grid = defaultdict(list)
    CELL = 200.0
    for w in osm:
        g = w.get("geometry") or []
        if len(g) < 2:
            continue
        xy = local_xy([p["lon"] for p in g], [p["lat"] for p in g], LAT0)
        for a, b in zip(xy[:-1], xy[1:]):
            for cx in range(int(min(a[0], b[0]) // CELL) - 1, int(max(a[0], b[0]) // CELL) + 2):
                for cy in range(int(min(a[1], b[1]) // CELL) - 1, int(max(a[1], b[1]) // CELL) + 2):
                    grid[(cx, cy)].append((a, b))

    def in_tunnel(p):
        best = 1e9
        for a, b in grid.get((int(p[0] // CELL), int(p[1] // CELL)), ()):
            ab = b - a
            t = 0.0 if not ab.any() else max(0.0, min(1.0, float(np.dot(p - a, ab) / np.dot(ab, ab))))
            best = min(best, float(np.hypot(*(a + t * ab - p))))
        return best < TUNNEL_TOL_M

    segs = []
    for e in net["edges"]:
        eid, i0, i1, tracks, length, co = e
        op, gauge, elec = info.get(eid, (None, None, None))
        if gauge != "mm1435" or op not in OPERATORS:
            continue
        pts = np.array(list(zip(co[0::2], co[1::2])), float)
        n = max(1, int(round(length / TARGET_M)))
        for k, (piece, L) in enumerate(cut(pts, n)):
            # sample every 10 m to find tunnel parts; keep the open sub-polylines
            xy = local_xy(piece[:, 0], piece[:, 1], LAT0)
            dd = np.r_[0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
            m = max(2, int(dd[-1] / 10) + 1)
            s = np.linspace(0, dd[-1], m)
            lon = np.interp(s, dd, piece[:, 0]); lat = np.interp(s, dd, piece[:, 1])
            sxy = local_xy(lon, lat, LAT0)
            tun = np.array([in_tunnel(p) for p in sxy])
            open_parts, cur = [], []
            for j in range(m):
                if not tun[j]:
                    cur.append([round(float(lon[j]), 6), round(float(lat[j]), 6)])
                elif cur:
                    if len(cur) > 1: open_parts.append(cur)
                    cur = []
            if len(cur) > 1:
                open_parts.append(cur)
            segs.append({
                "id": f"{eid}-{k}", "edge": eid, "k": k, "n": n,
                "from": nodes[i0][1], "to": nodes[i1][1], "from_id": nodes[i0][0], "to_id": nodes[i1][0],
                "op": op, "tracks": tracks, "len": round(L, 1),
                "tunnel": round(float(tun.mean()), 3),
                "coords": [[round(float(x), 6), round(float(y), 6)] for x, y in piece],
                "open": open_parts,
            })
    # smooth within an edge: base tunnels where OSM and BAV geometry drift apart by more than the tolerance
    import re
    inside = re.compile(r"^(Mitholz \(Spw\)|Ferden \(Spw\)|Lötschen \(Abzw\)|Sedrun SMF.*|Faido SMF.*|Pozzo Negro.*|Sarè \(dira\))$")
    by_edge = defaultdict(list)
    for s in segs:
        by_edge[s["edge"]].append(s)
    for e, ss in by_edge.items():
        mean = sum(x["tunnel"] * x["len"] for x in ss) / sum(x["len"] for x in ss)
        if mean >= 0.6 or (inside.match(ss[0]["from"]) and inside.match(ss[0]["to"])):
            for x in ss:
                x["tunnel"], x["open"] = 1.0, []
    dump(segs, WORK / "segments.json")
    km = sum(s["len"] for s in segs) / 1000
    print(f"{len(segs)} segments, {km:.0f} km, tunnel km {sum(s['len']*s['tunnel'] for s in segs)/1000:.0f}")


if __name__ == "__main__":
    main()
