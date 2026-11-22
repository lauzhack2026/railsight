"""Steps 2 and 3: a small language model reads the condensed numbers (batched), and a vision model looks at one
aerial crop only where the text step is unsure or the risk is high. Every call is logged with tokens, cost, latency."""
import base64, io, json, math, os, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
import numpy as np
from PIL import Image, ImageDraw

from common import APPDATA, ROOT, WORK, Fetcher, dump, lonlat_to_px, m_per_px

MODEL = "claude-haiku-5-5"
PRICE = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}  # $ per MTok in / out
CROPS = ROOT / "app" / "public" / "crops"
CROPS.mkdir(parents=True, exist_ok=True)
LOG = WORK / "calls.jsonl"


def load_env():
    p = Path.home() / ".config/lauzhack/.env"   # local convenience; otherwise ANTHROPIC_API_KEY from the environment
    if os.environ.get("ANTHROPIC_API_KEY") or not p.exists():
        return
    for line in p.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"')


def log_call(kind, model, usage, dt, n_items, extra=None):
    cin = usage.input_tokens + (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0)
    pin, pout = PRICE[model]
    cost = (cin * pin + usage.output_tokens * pout) / 1e6
    rec = {"kind": kind, "model": model, "in": cin, "out": usage.output_tokens, "s": round(dt, 2),
           "items": n_items, "usd": round(cost, 7), "t": time.time(), **(extra or {})}
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


# ---------------------------------------------------------------- gate 0: no model
def rule_score(f):
    """Transparent classical score on step 1's numbers (also the no-model ablation in the evaluation)."""
    tree = min(1.0, 0.6 * min(1.0, f["treeline_share"] / 0.6) + 0.2 * min(1.0, f["canopy_0_15"] / 0.3)
               + 0.2 * min(1.0, f["canopy_15_35"] / 0.3))
    slope = 0.6 * f["steep30_150"] + 0.4 * min(1.0, 2 * f["steep40_150"])
    proc = max(f["rock_60"], f["slide_60"], f["debris_60"])
    water = f["flood_60"]
    return round(100 * min(1.0, 0.45 * tree + 0.30 * slope + 0.15 * min(1.0, 2 * proc) + 0.10 * min(1.0, 2 * water)), 1)


def is_quiet(f, hist):
    """Nothing near the track that a model could weigh: no tall trees that reach it, flat, outside every hazard area."""
    return (f["treeline_share"] < 0.1 and f["canopy_0_15"] < 0.1 and f["canopy_15_35"] < 0.1
            and f["steep30_150"] < 0.05 and max(f["rock_60"], f["slide_60"], f["debris_60"]) < 0.02
            and f["flood_60"] < 0.05 and not hist)


SYSTEM = """You rate the natural-hazard exposure of ~1 km railway segments in Switzerland for an infrastructure insurer.
Each segment comes as measured numbers (no images):
- open_m: metres of open track (the rest is in tunnels)
- tr15: share of the ground 4-15 m from the track covered by trees at least 5 m tall; tr35: share of the ground 15-35 m away covered by trees at least 15 m tall (both from the national vegetation height model, derived from aerial stereo images)
- treeh: height of the taller trees within 20 m of the track (90th percentile, metres, from 10 m height classes)
- reach: share of the track length with trees tall enough to hit it if they fall
- st30 / st40: share of the terrain within 150 m that is steeper than 30 / 40 degrees
- rock / slide / debris: share of the 60 m corridor inside the federal SilvaProtect-CH process areas for rockfall / shallow landslides / debris flows
- flood: share of the 60 m corridor inside the 100-year flood area (Aquaprotect)
- hist: disruptive natural-hazard incidents on this station-to-station section in 2024-2025 (T tree/storm, S slope, W water, O obstacle on the track), shared by all segments of the section; "-" when none
For every segment return:
- risk: 0-100 on one absolute scale shared by every segment of the network (batches are mixed, so never rank within the batch). Anchors: flat open farmland or built-up area with no tall trees near the track: 5-10. Scattered trees that can reach part of the track, flat ground: 15-30. Tall trees that can reach most of the track, or forest on both sides, flat ground: 35-55. Forest on steep ground, or a large share inside rockfall, landslide or debris-flow areas: 55-80. Steep rock terrain inside rockfall areas right above the track, or several of these together: 80-100. Past incidents on the section raise the risk by 5-15.
- hazard: the dominant one (tree, rock, slide, debris, flood, none)
- confidence: low, medium or high
- why: at most 14 words, citing the decisive numbers in plain language
- look: true only if one look at the aerial image would settle a concrete doubt (for example a tree line whose distance to the track is unclear, or steep terrain where it is unclear whether bare rock sits above the track). Keep it rare."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["id", "risk", "hazard", "confidence", "why", "look"],
                               "properties": {"id": {"type": "string"}, "risk": {"type": "integer"},
                                              "hazard": {"type": "string", "enum": ["tree", "rock", "slide", "debris", "flood", "none"]},
                                              "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                                              "why": {"type": "string"}, "look": {"type": "boolean"}}}}}}


def row(i, f, hist):
    h = "".join(f"{k}{v}" for k, v in sorted(hist.items())) or "-"
    p = lambda x: int(round(100 * x))
    return (f"s{i}|{f['open_m']}|{p(f['canopy_0_15'])}|{p(f['canopy_15_35'])}|{f.get('tree_h', 0)}|{p(f['treeline_share'])}|"
            f"{p(f['steep30_150'])}|{p(f['steep40_150'])}|{p(f['rock_60'])}|{p(f['slide_60'])}|{p(f['debris_60'])}|"
            f"{p(f['flood_60'])}|{h}")


def text_batch(client, batch, use_hist=True, tag="text"):
    lines = ["id|open_m|tr15|tr35|treeh|reach|st30|st40|rock|slide|debris|flood|hist  (shares in %, treeh in m)"]
    for i, (sid, f, hist) in enumerate(batch):
        lines.append(row(i, f, hist if use_hist else {}))
    t0 = time.time()
    for attempt in range(3):
        try:
            r = client.messages.create(model=MODEL, max_tokens=4000, system=SYSTEM,
                                       thinking={"type": "disabled"},
                                       output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                                       messages=[{"role": "user", "content": "\n".join(lines)}])
            break
        except anthropic.APIError as e:
            print("retry", e); time.sleep(3 * (attempt + 1))
    else:
        return {}
    rec = log_call(tag, MODEL, r.usage, time.time() - t0, len(batch))
    data = json.loads(next(b.text for b in r.content if b.type == "text"))
    out = {}
    for it in data["items"]:
        try:
            i = int(it["id"].lstrip("s"))
        except ValueError:
            continue
        if 0 <= i < len(batch):
            out[batch[i][0]] = {**{k: it[k] for k in ("risk", "hazard", "confidence", "why", "look")},
                                "usd": round(rec["usd"] / len(batch), 7)}
    return out


# ---------------------------------------------------------------- step 3: vision on one crop
VISION_Q = {
    "tree": "Do tree crowns stand close enough to the track (within roughly one tree height, 20-30 m) that a falling tree could block it?",
    "rock": "Is there bare rock, a cliff or scree slope above the track from which stones could reach it?",
    "slide": "Does the track run along or below a steep, unstable-looking slope (bare soil, scars, gullies)?",
    "debris": "Does a torrent channel or debris cone cross or approach the track?",
    "flood": "Does the track run low next to a river or lake, without visible protection, so that flood water could reach it?",
    "none": "Is there any visible natural hazard to the track (trees, rock, unstable slope, water)?",
}


def crop_for(seg, f, fetcher, size_m=320, z=17):
    lon, lat = f.get("hot") or seg["coords"][len(seg["coords"]) // 2]
    res = m_per_px(lat, z)
    cx, cy = lonlat_to_px(lon, lat, z)
    half = size_m / res / 2
    x0, y0, x1, y1 = int(cx - half), int(cy - half), int(cx + half), int(cy + half)
    img = Image.new("RGB", (x1 - x0, y1 - y0))
    for tx in range(x0 // 256, (x1 - 1) // 256 + 1):
        for ty in range(y0 // 256, (y1 - 1) // 256 + 1):
            p = fetcher.get("ortho17", z, tx, ty, layer="ch.swisstopo.swissimage", ext="jpeg")
            if p and p.stat().st_size:
                img.paste(Image.open(p).convert("RGB"), (tx * 256 - x0, ty * 256 - y0))
    clean = img.copy()
    d = ImageDraw.Draw(img)
    for part in seg["open"]:
        pts = [lonlat_to_px(a, b, z) for a, b in part]
        d.line([(x - x0, y - y0) for x, y in pts], fill=(255, 0, 200), width=2)
    return img, clean, (x0, y0, z)


VSCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "risk", "seen"], "properties": {
    "verdict": {"type": "string", "enum": ["confirmed", "rejected", "unclear"]},
    "risk": {"type": "integer"}, "seen": {"type": "string"}}}


def vision_one(client, seg, f, a, fetcher, model=MODEL, tag="vision"):
    img, clean, _ = crop_for(seg, f, fetcher)
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=85)
    img.save(CROPS / f"{seg['id']}.jpg", "JPEG", quality=82)
    q = VISION_Q.get(a["hazard"], VISION_Q["none"])
    prompt = (f"Aerial orthophoto, north up, about 320 m across (0.8 m per pixel). The thin magenta line is the railway track.\n"
              f"A text-only screening rated this spot {a['risk']}/100 for {a['hazard']} hazard: \"{a['why']}\".\n"
              f"Question: {q}\nReply with verdict (confirmed / rejected / unclear for that hazard), a revised risk 0-100, "
              f"and what you see in at most 14 words.")
    t0 = time.time()
    r = client.messages.create(model=model, max_tokens=600, thinking={"type": "disabled"},
                               output_config={"format": {"type": "json_schema", "schema": VSCHEMA}},
                               messages=[{"role": "user", "content": [
                                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                                "data": base64.b64encode(buf.getvalue()).decode()}},
                                   {"type": "text", "text": prompt}]}])
    rec = log_call(tag, model, r.usage, time.time() - t0, 1, {"seg": seg["id"], "px": list(img.size)})
    return {**json.loads(next(b.text for b in r.content if b.type == "text")), "usd": rec["usd"], "tokens": rec["in"] + rec["out"]}


def history_by_segment(min_year=2024, max_year=2025):
    hist = defaultdict(lambda: defaultdict(int))
    code = {"tree": "T", "slope": "S", "water": "W", "obstacle": "O"}
    for o in json.load(open(WORK / "incidents_mapped.json")):
        if o["cls"] and min_year <= o["year"] <= max_year:
            for sid in set(o["segments"]):
                hist[sid][code[o["cls"]]] += 1
    return hist


def main(vision_share=0.08, batch_size=30, use_hist=True, out_name="assess.json", tag="text", vision=True):
    load_env()
    client = anthropic.Anthropic()
    segs = {s["id"]: s for s in json.load(open(WORK / "segments.json"))}
    feats = json.load(open(WORK / "features.json"))
    hist = history_by_segment()
    out_p = WORK / out_name
    res = json.load(open(out_p)) if out_p.exists() else {}
    todo = []
    for sid, f in feats.items():
        if sid in res:
            continue
        h = dict(hist.get(sid, {})) if use_hist else {}
        if f.get("tunnel_only"):
            res[sid] = {"gate": "tunnel", "risk": 0, "rule": 0}
        elif is_quiet(f, h):
            res[sid] = {"gate": "quiet", "risk": round(rule_score(f)), "rule": rule_score(f)}
        else:
            todo.append((sid, f, h))
    import random
    random.Random(42).shuffle(todo)   # mixed batches: no region dominates one call
    print(f"text step: {len(todo)} segments in batches of {batch_size}", flush=True)
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    with ThreadPoolExecutor(4) as ex:
        for b, out in zip(batches, ex.map(lambda b: text_batch(client, b, use_hist, tag), batches)):
            for sid, f, h in b:
                if sid in out:
                    res[sid] = {"gate": "text", "rule": rule_score(f), **out[sid]}
                else:
                    res[sid] = {"gate": "failed", "risk": round(rule_score(f)), "rule": rule_score(f)}
            dump(res, out_p)
    if vision:
        cand = [(sid, a) for sid, a in res.items() if a["gate"] == "text" and "vision" not in a
                and (a.get("look") or (a["risk"] >= 70 and a["confidence"] != "high"))]
        cand.sort(key=lambda x: -x[1]["risk"])
        n_text = sum(1 for a in res.values() if a["gate"] == "text")
        cap = int(vision_share * n_text)
        cand = cand[:cap]
        print(f"vision step: {len(cand)} of {n_text} text-assessed segments", flush=True)
        fetcher = Fetcher(rate=10)

        def run(x):
            sid, a = x
            try:
                return sid, vision_one(client, segs[sid], feats[sid], a, fetcher)
            except Exception as e:
                print("vision failed", sid, e); return sid, None
        with ThreadPoolExecutor(4) as ex:
            for sid, v in ex.map(run, cand):
                if v:
                    a = res[sid]; a["vision"] = v; a["risk_text"] = a["risk"]
                    if v["verdict"] == "confirmed":
                        a["risk"] = max(a["risk"], v["risk"])
                    elif v["verdict"] == "rejected":
                        a["risk"] = min(a["risk"], v["risk"])
        dump(res, out_p)
    from collections import Counter
    print(Counter(a["gate"] for a in res.values()))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "terrain":
        # evaluation variant: the same text step without any incident history in its input, no vision step
        main(use_hist=False, out_name="assess_terrain.json", tag="text_terrain", vision=False)
    else:
        main()
