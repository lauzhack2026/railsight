"""The baseline we compare against: every km of track sent as one aerial image to a frontier vision model.
Run on a small random sample to MEASURE its tokens and latency (not estimate them)."""
import base64, io, json, random, sys, time

import anthropic
from PIL import Image, ImageDraw

from assess import PRICE, VSCHEMA, load_env, log_call
from common import WORK, Fetcher, lonlat_to_px

MODEL = "claude-opus-5-5"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["risk", "hazard", "why"], "properties": {
    "risk": {"type": "integer"}, "hazard": {"type": "string", "enum": ["tree", "rock", "slide", "debris", "flood", "none"]},
    "why": {"type": "string"}}}


def segment_image(seg, fetcher, z=17, max_px=1024, margin_px=80):
    pts = [lonlat_to_px(a, b, z) for a, b in seg["coords"]]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x0, y0, x1, y1 = int(min(xs)) - margin_px, int(min(ys)) - margin_px, int(max(xs)) + margin_px, int(max(ys)) + margin_px
    img = Image.new("RGB", (x1 - x0, y1 - y0))
    for tx in range(x0 // 256, (x1 - 1) // 256 + 1):
        for ty in range(y0 // 256, (y1 - 1) // 256 + 1):
            p = fetcher.get("ortho17", z, tx, ty, layer="ch.swisstopo.swissimage", ext="jpeg")
            if p and p.stat().st_size:
                img.paste(Image.open(p).convert("RGB"), (tx * 256 - x0, ty * 256 - y0))
    d = ImageDraw.Draw(img)
    d.line([(x - x0, y - y0) for x, y in pts], fill=(255, 0, 200), width=2)
    img.thumbnail((max_px, max_px))
    return img


def main(n=12):
    load_env()
    client = anthropic.Anthropic()
    feats = json.load(open(WORK / "features.json"))
    segs = [s for s in json.load(open(WORK / "segments.json")) if s["id"] in feats and not feats[s["id"]].get("tunnel_only")]
    random.Random(3).shuffle(segs)
    f = Fetcher(rate=8)
    out = {}
    for seg in segs[:n]:
        img = segment_image(seg, f)
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=85)
        t0 = time.time()
        r = client.messages.create(model=MODEL, max_tokens=4000, output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                                   messages=[{"role": "user", "content": [
                                       {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
                                       {"type": "text", "text": "Aerial orthophoto of about 1 km of Swiss railway (magenta line). Rate the natural-hazard exposure of this track 0-100 (trees that can fall on it, rockfall, landslides, debris flows, floods), name the dominant hazard and say why in at most 14 words."}]}])
        rec = log_call("naive", MODEL, r.usage, time.time() - t0, 1, {"seg": seg["id"], "px": list(img.size)})
        out[seg["id"]] = json.loads(next(b.text for b in r.content if b.type == "text"))
        print(rec["in"], rec["out"], rec["s"], rec["usd"], out[seg["id"]], flush=True)
    json.dump(out, open(WORK / "naive.json", "w"), indent=1)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 12)
