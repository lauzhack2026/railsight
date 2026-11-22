"""Live re-scan of one segment, for the demo: runs the three steps again and reports what each one cost.
GET /api/scan/<segment id>  ->  {steps: [...], result: {...}}   (local only: 127.0.0.1)"""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic

import assess
from common import LAYERS, WORK, Fetcher
from features import segment_features
from fetch import tiles_for

assess.load_env()
CLIENT = anthropic.Anthropic()
SEGS = {s["id"]: s for s in json.load(open(WORK / "segments.json"))}
HIST = assess.history_by_segment()
FETCHER = Fetcher(rate=8)


def scan(sid):
    seg = SEGS[sid]
    steps = []
    t0 = time.perf_counter()
    n_tiles = 0
    for key in LAYERS:
        z = LAYERS[key][2]
        for tx, ty in tiles_for(seg, key):
            FETCHER.get(key, z, tx, ty); n_tiles += 1
    t1 = time.perf_counter()
    f, _ = segment_features(seg)
    t2 = time.perf_counter()
    steps.append({"step": 1, "name": "Image processing", "ms": round(1000 * (t2 - t1)), "tiles": n_tiles,
                  "tiles_ms": round(1000 * (t1 - t0)), "usd": 0})
    h = dict(HIST.get(sid, {}))
    if f.get("tunnel_only"):
        return {"steps": steps + [{"step": 2, "skipped": "tunnel"}, {"step": 3, "skipped": "tunnel"}], "features": f, "result": {"gate": "tunnel", "risk": 0}}
    if assess.is_quiet(f, h):
        r = round(assess.rule_score(f))
        return {"steps": steps + [{"step": 2, "skipped": "nothing near the track"}, {"step": 3, "skipped": "not needed"}], "features": f,
                "result": {"gate": "quiet", "risk": r}}
    n0 = sum(1 for _ in open(assess.LOG)) if assess.LOG.exists() else 0
    t3 = time.perf_counter()
    out = assess.text_batch(CLIENT, [(sid, f, h)], tag="live-text")
    t4 = time.perf_counter()
    rec = json.loads(open(assess.LOG).read().splitlines()[-1])
    a = out.get(sid)
    if not a:
        return {"steps": steps, "error": "text model failed"}
    steps.append({"step": 2, "name": "Small language model", "ms": round(1000 * (t4 - t3)), "in": rec["in"], "out": rec["out"], "usd": rec["usd"]})
    res = {"gate": "text", **a}
    if a["look"] or (a["risk"] >= 70 and a["confidence"] != "high"):
        t5 = time.perf_counter()
        v = assess.vision_one(CLIENT, seg, f, a, FETCHER, tag="live-vision")
        t6 = time.perf_counter()
        rec = json.loads(open(assess.LOG).read().splitlines()[-1])
        steps.append({"step": 3, "name": "Vision model", "ms": round(1000 * (t6 - t5)), "in": rec["in"], "out": rec["out"], "usd": rec["usd"]})
        res["vision"] = v
    else:
        steps.append({"step": 3, "skipped": "the numbers were clear enough"})
    return {"steps": steps, "features": f, "result": res}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok"); return
        if not self.path.startswith("/api/scan/"):
            self.send_error(404); return
        sid = self.path.rsplit("/", 1)[-1]
        if sid not in SEGS:
            self.send_error(404); return
        try:
            body = json.dumps(scan(sid)).encode()
            self.send_response(200)
        except Exception as e:  # keep the demo alive
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5482"))
    print(f"railsight live scan on 127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
