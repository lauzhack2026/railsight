"""Why step 1 reads the vegetation height model instead of classifying the orthophoto.
Compares the first excess-green canopy classifier on SWISSIMAGE (features.canopy_mask) with the national forest
inventory's vegetation height model (trees >= 3 m) within 4-35 m of the track, on random segments."""
import json, random

import numpy as np

from common import WORK, dump, mosaic
from features import canopy_mask, track_raster, tree_heights


def main(n=60):
    feats = json.load(open(WORK / "features.json"))
    segs = [s for s in json.load(open(WORK / "segments.json")) if s["id"] in feats and not feats[s["id"]].get("tunnel_only")]
    random.Random(5).shuffle(segs)
    tp = fp = fn = 0; used = 0
    for s in segs[:n]:
        (x0, y0, x1, y1), dist, res, _ = track_raster(s["open"], 16, 40)
        rgb, _ = mosaic("ortho", 16, x0, y0, x1, y1, "RGB")
        vh, _ = mosaic("vhm", 16, x0, y0, x1, y1, "RGBA")
        if rgb.max() == 0 or vh[..., 3].max() == 0:
            continue
        used += 1
        band = (dist >= 4) & (dist < 35)
        can = canopy_mask(rgb) & band
        lo, _ = tree_heights(vh)
        trees = (lo >= 3) & band
        tp += int((can & trees).sum()); fp += int((can & ~trees).sum()); fn += int((~can & trees).sum())
    out = {"segments": used, "trees_found": round(tp / max(1, tp + fn), 3), "canopy_really_trees": round(tp / max(1, tp + fp), 3)}
    dump(out, WORK / "canopy_validation.json", indent=1)
    print(out)


if __name__ == "__main__":
    main()
