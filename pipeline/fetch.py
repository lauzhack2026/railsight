"""Fetch (and cache) every WMTS tile the scan needs: only tiles touching each layer's corridor around open track.
Demo regions first, then the rest of the network. One process, a shared rate limit."""
import json, math, sys, time
from concurrent.futures import ThreadPoolExecutor

from common import LAYERS, STEP1, WORK, Fetcher, lonlat_to_px, m_per_px

REGIONS = {  # lon0, lat0, lon1, lat1
    "gotthard": (8.45, 46.30, 9.05, 47.10),
    "loetschberg": (7.55, 46.25, 8.10, 46.72),
    "mittelland": (7.40, 46.90, 8.30, 47.45),
}


def region_of(s):
    lon, lat = s["coords"][len(s["coords"]) // 2]
    for name, (a, b, c, d) in REGIONS.items():
        if a <= lon <= c and b <= lat <= d:
            return name
    return None


def tiles_for(seg, key):
    _, _, z, buf = LAYERS[key]
    out = set()
    for part in seg["open"]:
        for (lo1, la1), (lo2, la2) in zip(part, part[1:]):
            r = buf / m_per_px(la1, z)
            for t in (0.0, 0.5, 1.0):
                x, y = lonlat_to_px(lo1 + (lo2 - lo1) * t, la1 + (la2 - la1) * t, z)
                for tx in range(int((x - r) // 256), int((x + r) // 256) + 1):
                    for ty in range(int((y - r) // 256), int((y + r) // 256) + 1):
                        out.add((tx, ty))
    return out


def main(rate=12.0, repair=False):
    segs = json.load(open(WORK / "segments.json"))
    order = {"gotthard": 0, "loetschberg": 1, "mittelland": 2, None: 3}
    segs.sort(key=lambda s: order[region_of(s)])
    jobs, seen = [], set()
    for s in segs:
        for key in STEP1:
            for tx, ty in sorted(tiles_for(s, key)):
                z = LAYERS[key][2]
                if (key, z, tx, ty) not in seen:
                    seen.add((key, z, tx, ty)); jobs.append((key, z, tx, ty))
    from collections import Counter
    print("tiles per layer", Counter(j[0] for j in jobs), "total", len(jobs), flush=True)
    f = Fetcher(rate=rate, mark_failed=repair)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(6) as ex:
        for _ in ex.map(lambda j: f.get(j[0], j[1], j[2], j[3]), jobs):
            done += 1
            if done % 1000 == 0:
                el = time.time() - t0
                print(f"{done}/{len(jobs)} {el:.0f}s {f.stats}", flush=True)
    print("done", f.stats, f"{time.time()-t0:.0f}s", flush=True)
    # bytes of every tile step 1 uses (also those fetched by an earlier run)
    from common import tile_path
    size = sum(tile_path(k, z, x, y).stat().st_size for k, z, x, y in jobs if tile_path(k, z, x, y).exists())
    stats = {"tiles": len(jobs), "bytes": size, "seconds": round(time.time() - t0), **{k: v for k, v in f.stats.items() if k != "bytes"}}
    json.dump(stats, open(WORK / ("fetch_stats_repair.json" if repair else "fetch_stats.json"), "w"))


if __name__ == "__main__":
    # "repair": a second pass that retries the tiles that failed and marks the ones that keep failing as empty
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 12.0, repair=len(sys.argv) > 2 and sys.argv[2] == "repair")
