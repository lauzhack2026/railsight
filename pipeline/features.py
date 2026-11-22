"""Step 1 (no model): condense aerial imagery and terrain/hazard layers into a few numbers per segment.

vhm    vegetation height model of the national forest inventory (derived from the SWISSIMAGE aerial stereo images),
       z16 (~1.6 m/px), decoded from its legend colours to a height per pixel: which trees are tall enough to reach
       the track if they fall (an earlier excess-green classifier on the orthophoto was dropped: it took meadows and
       green rivers for trees; see validate_canopy.py)
slope  slope classes over 30 deg (z15, ~3.3 m/px) within 150 m of the track
rock / slide / debris  SilvaProtect-CH process areas (z14, ~6.5 m/px) within 60 m
flood  Aquaprotect 100-year flood areas (z14) within 60 m
"""
import json, math, sys, time

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from common import LAYERS, WORK, dump, lonlat_to_px, m_per_px, mosaic, px_to_lonlat


def track_raster(parts, z, margin_m):
    lat = np.mean([p[1] for part in parts for p in part])
    res = m_per_px(lat, z)
    pts = [[lonlat_to_px(lon, la, z) for lon, la in part] for part in parts]
    xs = [p[0] for part in pts for p in part]; ys = [p[1] for part in pts for p in part]
    m = margin_m / res + 3
    x0, y0, x1, y1 = math.floor(min(xs) - m), math.floor(min(ys) - m), math.ceil(max(xs) + m), math.ceil(max(ys) + m)
    img = Image.new("L", (x1 - x0, y1 - y0), 0)
    d = ImageDraw.Draw(img)
    for part in pts:
        d.line([(x - x0, y - y0) for x, y in part], fill=255, width=1)
    track = np.asarray(img) > 0
    dist = ndimage.distance_transform_edt(~track) * res
    return (x0, y0, x1, y1), dist, res, pts


# legend of ch.bafu.landesforstinventar-vegetationshoehenmodell: colour -> (lower bound, typical height) in metres
# (colour, lower bound, upper bound of the class)
VHM_LEGEND = [((255, 255, 255), 0, 0), ((255, 255, 171), 1, 2), ((255, 255, 87), 2, 3), ((236, 252, 0), 3, 5),
              ((171, 245, 0), 5, 15), ((102, 235, 0), 15, 20), ((0, 201, 30), 20, 30), ((0, 158, 140), 30, 40),
              ((0, 105, 92), 40, 50)]


def tree_heights(rgba):
    """Per pixel: (lower, upper bound) of the vegetation height class in metres, by nearest legend colour."""
    a = rgba[..., :3].astype(np.int32)
    cols = np.array([c for c, _, _ in VHM_LEGEND], np.int32)
    d = ((a[..., None, :] - cols[None, None, :, :]) ** 2).sum(-1)
    k = d.argmin(-1)
    lo = np.array([l for _, l, _ in VHM_LEGEND], np.float32)[k]
    mid = np.array([m for _, _, m in VHM_LEGEND], np.float32)[k]
    none = rgba[..., 3] < 100
    lo[none] = 0; mid[none] = 0
    return lo, mid


def canopy_mask(rgb):
    a = rgb.astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    s = r + g + b + 1e-3
    exg = (2 * g - r - b) / s
    v = s / 3
    mu = ndimage.uniform_filter(v, 5); sd = np.sqrt(np.maximum(ndimage.uniform_filter(v * v, 5) - mu * mu, 0))
    veg = exg > 0.045
    # trees: vegetation that is dark (crowns and their shadows) or textured; smooth bright green is grass
    tree = veg & ((v < 92) | ((sd > 11) & (v < 140)))
    # dark shadow pixels next to vegetation also belong to crowns
    shadow = (v < 45) & ndimage.binary_dilation(tree, iterations=2)
    m = ndimage.binary_opening(tree | shadow, iterations=1)
    return m


def slope_class(rgba):
    a = rgba.astype(np.int16)
    op = a[..., 3] > 100
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    cls = np.zeros(op.shape, np.int8)
    cls[op] = 1                                             # >= 30 deg (yellow)
    cls[op & (g < 170) & (r > 200) & (b < 80)] = 2          # 35-40 (orange)
    cls[op & (g < 60)] = 3                                  # 40-45 (crimson)
    cls[op & (b > 140) & (r > 150) & (g > 100)] = 4         # > 45 (violet)
    return cls


def band_share(mask, dist, lo, hi):
    sel = (dist >= lo) & (dist < hi)
    n = int(sel.sum())
    return float(mask[sel].mean()) if n else 0.0


def resample_dist(parts, z_from_box, z, margin):
    return track_raster(parts, z, margin)


def tiles_ready(seg):
    from fetch import tiles_for
    from common import STEP1, tile_path
    for key in STEP1:
        z = LAYERS[key][2]
        for tx, ty in tiles_for(seg, key):
            if not tile_path(key, z, tx, ty).exists():
                return False
    return True


def segment_features(seg, debug=False):
    parts = seg["open"]
    f = {"open_m": round(seg["len"] * (1 - seg["tunnel"]))}
    if f["open_m"] < 60 or not parts:
        f["tunnel_only"] = True
        return f, None
    if not tiles_ready(seg):
        raise FileNotFoundError("tiles not cached yet")
    # ---- trees: vegetation height model
    _, _, z, buf = LAYERS["vhm"]
    (x0, y0, x1, y1), dist, res, pts = track_raster(parts, z, buf)
    vh, _ = mosaic("vhm", z, x0, y0, x1, y1, "RGBA")
    lo, mid = tree_heights(vh)
    f["canopy_0_15"] = round(band_share(lo >= 5, dist, 4, 15), 3)           # trees >= 5 m, 4-15 m from the track
    f["canopy_15_35"] = round(band_share(lo >= 15, dist, 15, 35), 3)        # trees >= 15 m, 15-35 m away
    reach = (mid >= dist) & (dist >= 3) & (lo >= 3)                          # may hit the track if it falls towards it
    near = (dist >= 3) & (dist < 40)
    nh = ((lo + mid) / 2)[(dist >= 3) & (dist < 20)]                        # class centre, e.g. 10 m for 5-15 m
    f["tree_h"] = int(round(float(np.percentile(nh, 90)))) if nh.size else 0
    # share of track length with trees that can reach it: >15% of the nearby ground within a 50 m window
    k = max(3, int(round(50 / res)) | 1)
    local = ndimage.uniform_filter(reach.astype(np.float32), k) / np.maximum(ndimage.uniform_filter(near.astype(np.float32), k), 1e-3)
    samples = []
    for part in pts:
        for (xa, ya), (xb, yb) in zip(part, part[1:]):
            L = math.hypot(xb - xa, yb - ya); n = max(1, int(L * res / 10))
            for i in range(n):
                samples.append((xa + (xb - xa) * i / n - x0, ya + (yb - ya) * i / n - y0))
    sv = np.array([local[min(int(y), local.shape[0] - 1), min(int(x), local.shape[1] - 1)] for x, y in samples]) if samples else np.zeros(1)
    f["treeline_share"] = round(float((sv > 0.10).mean()), 3)
    hot_tree = samples[int(np.argmax(sv))] if samples else None
    hot_tree_v = float(sv.max()) if samples else 0.0

    # ---- slope classes
    _, _, zs, bufs = LAYERS["slope"]
    (sx0, sy0, sx1, sy1), sdist, sres, spts = track_raster(parts, zs, bufs)
    srgba, _ = mosaic("slope", zs, sx0, sy0, sx1, sy1, "RGBA")
    sc = slope_class(srgba)
    f["steep30_150"] = round(band_share(sc >= 1, sdist, 0, 150), 3)
    f["steep40_150"] = round(band_share(sc >= 3, sdist, 0, 150), 3)
    f["steep35_50"] = round(band_share(sc >= 2, sdist, 0, 50), 3)
    # ---- official process areas
    for key in ("rock", "slide", "debris", "flood"):
        _, _, zh, bufh = LAYERS[key]
        (hx0, hy0, hx1, hy1), hdist, hres, _ = track_raster(parts, zh, bufh)
        h, _ = mosaic(key, zh, hx0, hy0, hx1, hy1, "RGBA")
        m = h[..., 3] > 60
        f[f"{key}_60"] = round(band_share(m, hdist, 0, 60), 3)
        f[f"{key}_touch"] = bool((m & (hdist < 15)).any())
    # hot spot for the vision step: where the classical step sees the most canopy at the track
    if hot_tree is not None:
        lon, lat = px_to_lonlat(hot_tree[0] + x0, hot_tree[1] + y0, z)
        f["hot"] = [round(lon, 6), round(lat, 6)]
        f["hot_canopy"] = round(hot_tree_v, 3)
    return f, None


def main(limit=None, only=None):
    segs = json.load(open(WORK / "segments.json"))
    if only:
        segs = [s for s in segs if s["id"] in only]
    out_p = WORK / "features.json"
    feats = json.load(open(out_p)) if out_p.exists() and not only else {}
    t0 = time.time(); n = 0; cpu = 0.0
    for s in segs:
        if s["id"] in feats and not only:
            continue
        c0 = time.perf_counter()
        try:
            f, dbg = segment_features(s, debug=bool(only))
        except FileNotFoundError:  # tiles not fetched yet: next run
            continue
        cpu += time.perf_counter() - c0
        if f is None:
            continue
        feats[s["id"]] = f; n += 1
        if dbg is not None:
            Image.fromarray(dbg).save(WORK / f"dbg_{s['id']}.png")
        if limit and n >= limit:
            break
        if n % 200 == 0:
            print(n, f"{time.time()-t0:.0f}s", flush=True); dump(feats, out_p)
    if not only:
        dump(feats, out_p)
        if n > 100:
            dump({"segments": n, "seconds": round(cpu, 1), "ms_per_segment": round(1000 * cpu / n)}, WORK / "features_stats.json")
    print(f"{n} segments in {time.time()-t0:.1f}s, compute {cpu:.1f}s ({1000*cpu/max(n,1):.0f} ms/segment)")
    return feats


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        print(json.dumps(main(only=set(sys.argv[2:])), indent=1))
    else:
        main()
