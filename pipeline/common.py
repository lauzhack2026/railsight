"""Shared paths, Web-Mercator tile maths and a polite, cached WMTS tile fetcher."""
import io, json, math, os, threading, time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
TILES = CACHE / "tiles"
WORK = CACHE / "work"          # intermediate pipeline outputs (not committed)

APPDATA = ROOT / "app" / "public" / "data"
for p in (CACHE, TILES, WORK, APPDATA):
    p.mkdir(parents=True, exist_ok=True)

NETWORK = Path("/workspace/rail-twin/data/network.json.gz")
GPKG = Path("/workspace/sbb/geo/schienennetz_2056_de.gpkg")
INCIDENTS = Path("/workspace/sbb/history/incidents.json")
INCIDENT_TEXT = Path("/workspace/sbb/out/incidents.json")

UA = "railsight/0.1 (LauzHack 2026 research prototype; contact via github.com/LauzTakt)"
WMTS = "https://wmts.geo.admin.ch/1.0.0/{layer}/default/current/3857/{z}/{x}/{y}.{ext}"

# layer -> (file extension, zoom used, corridor half-width in metres)
LAYERS = {
    "ortho": ("ch.swisstopo.swissimage", "jpeg", 16, 40),
    "vhm": ("ch.bafu.landesforstinventar-vegetationshoehenmodell", "png", 16, 40),
    "slope": ("ch.swisstopo.hangneigung-ueber_30", "png", 15, 150),
    "rock": ("ch.bafu.silvaprotect-sturz", "png", 14, 60),
    "slide": ("ch.bafu.silvaprotect-hangmuren", "png", 14, 60),
    "debris": ("ch.bafu.silvaprotect-murgang", "png", 14, 60),
    "flood": ("ch.bafu.aquaprotect_100", "png", 14, 60),
}


STEP1 = ["vhm", "slope", "rock", "slide", "debris", "flood"]   # the layers step 1 reads ("ortho" only feeds step 3)


def lonlat_to_px(lon, lat, z):
    """Global Web-Mercator pixel coordinates (256 px tiles) at zoom z."""
    n = 256 * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def px_to_lonlat(x, y, z):
    n = 256 * 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def m_per_px(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / 2 ** z


def tile_path(key, z, x, y):
    layer, ext, _, _ = LAYERS[key] if key in LAYERS else (key, "jpeg", z, 0)
    return TILES / key / str(z) / str(x) / f"{y}.{ext}"


class Fetcher:
    """Cached tile fetcher with a global rate limit (requests per second) shared by all threads."""

    def __init__(self, rate=10.0, mark_failed=False):
        self.mark_failed = mark_failed
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.rate = rate
        self.lock = threading.Lock()
        self.next_t = 0.0
        self.stats = {"hit": 0, "fetched": 0, "bytes": 0, "errors": 0}

    def _wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next_t)
            self.next_t = t + 1.0 / self.rate
        d = t - time.monotonic()
        if d > 0:
            time.sleep(d)

    def get(self, key, z, x, y, layer=None, ext=None):
        if key in LAYERS:
            layer, ext, _, _ = LAYERS[key]
        p = TILES / key / str(z) / str(x) / f"{y}.{ext}"
        if p.exists():
            self.stats["hit"] += 1
            return p
        url = WMTS.format(layer=layer, z=z, x=x, y=y, ext=ext)
        for attempt in range(4):
            self._wait()
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code == 200:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    tmp = p.with_suffix(p.suffix + ".tmp")
                    tmp.write_bytes(r.content)
                    os.replace(tmp, p)
                    self.stats["fetched"] += 1
                    self.stats["bytes"] += len(r.content)
                    return p
                if r.status_code in (400, 404, 501):
                    # outside the layer's extent: store an empty marker
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b"")
                    return p
            except requests.RequestException:
                pass
            time.sleep(1.5 * (attempt + 1))
        self.stats["errors"] += 1
        if self.mark_failed:   # give up on this tile for good: an empty tile, counted as an error
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")
            return p
        return None


def load_tile(p, mode):
    if p is None or not p.exists() or p.stat().st_size == 0:
        return None
    try:
        return np.asarray(Image.open(p).convert(mode))
    except Exception:
        return None


def mosaic(key, z, x0, y0, x1, y1, mode, fetcher=None):
    """Mosaic of tiles covering global pixel box [x0,x1) x [y0,y1) at zoom z. Missing tiles are zeros."""
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int((x1 - 1) // 256), int((y1 - 1) // 256)
    ch = 3 if mode == "RGB" else 4
    out = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256, ch), np.uint8)
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            p = tile_path(key, z, tx, ty) if fetcher is None else fetcher.get(key, z, tx, ty)
            a = load_tile(p, mode)  # tiles outside the fetched corridor are left empty
            if a is not None:
                out[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = a
    ox, oy = tx0 * 256, ty0 * 256
    return out[int(y0 - oy):int(y1 - oy), int(x0 - ox):int(x1 - ox)], (int(x0), int(y0))


def dump(obj, path, indent=None):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":")))
    os.replace(tmp, path)
