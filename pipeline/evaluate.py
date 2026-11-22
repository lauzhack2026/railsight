"""Honest check: do natural-hazard incidents happen on the track the scan ranks riskiest?

Main check (more incidents): every natural-hazard incident since March 2024, against the TERRAIN-ONLY scan, whose
inputs contain no incident history at all (no leakage). Secondary: out-of-time, the incidents of 2026 against the full
scan, which saw the incidents of 2024-2025 as history.
Metric: share of incidents inside the top k of open track (k = 5, 10, 20 %). An incident is known per station-to-station
section only, so it is spread evenly over the open segments of its section. Random ranking catches k on average.
Interval: 95 % bootstrap over incidents."""
import json
from collections import defaultdict

import numpy as np

from common import WORK, dump

KS = [0.05, 0.10, 0.20]
REPS = 2000


def topk_weights(scores, k):
    """Fraction of each segment inside the top-k (ties at the cut-off share the remaining room)."""
    ids = list(scores)
    v = np.array([scores[i] for i in ids], float)
    n_top = k * len(ids)
    order = np.sort(v)[::-1]
    cut = order[max(0, int(np.ceil(n_top)) - 1)]
    above = (v > cut).sum(); tie = (v == cut).sum()
    w = np.where(v > cut, 1.0, np.where(v == cut, max(0.0, n_top - above) / tie, 0.0))
    return dict(zip(ids, w))


def share(incs, w):
    vals = np.array([np.mean([w[s] for s in segs]) for segs in incs]) if incs else np.zeros(0)
    if not len(vals):
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(7)
    boots = vals[rng.integers(0, len(vals), (REPS, len(vals)))].mean(1)
    return float(vals.mean()), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def dedupe(incs):
    """One event reported several times (same section within 3 days) counts once."""
    out, seen = [], []
    for o in sorted(incs, key=lambda o: o["start"]):
        key = frozenset(o["open"])
        if any(key == k and abs((np.datetime64(o["start"][:10]) - np.datetime64(d)).astype(int)) <= 3 for k, d in seen):
            continue
        seen.append((key, o["start"][:10])); out.append(o)
    return out


def table(incs, methods):
    """{method: {k: [share, lo, hi]}} and the k = 10 % rows for the bars."""
    tab, rows = {}, []
    for name, (scores, note) in methods.items():
        tab[name] = {}
        for k in KS:
            s, lo, hi = share([o["open"] for o in incs], topk_weights(scores, k))
            tab[name][f"{int(round(100 * k))}"] = [round(s, 3), round(lo, 3), round(hi, 3)]
        s, lo, hi = tab[name]["10"]
        rows.append({"name": name, "note": note, "share": s, "lo": lo, "hi": hi})
    return tab, rows


def main():
    feats = json.load(open(WORK / "features.json"))
    A = json.load(open(WORK / "assess.json"))
    T = json.load(open(WORK / "assess_terrain.json"))
    segs = {s["id"]: s for s in json.load(open(WORK / "segments.json"))}
    trains = json.load(open(WORK / "trains_per_edge.json"))
    edge_len = defaultdict(float)
    for s in segs.values():
        edge_len[s["edge"]] += s["len"]
    universe = [i for i, f in feats.items() if not f.get("tunnel_only") and i in A and i in T]
    U = set(universe)
    incs = json.load(open(WORK / "incidents_mapped.json"))
    for o in incs:
        o["open"] = [s for s in dict.fromkeys(o["segments"]) if s in U]
    nat = [o for o in incs if o["cls"] and o["open"]]
    allyrs = dedupe(nat)
    test = dedupe([o for o in nat if o["year"] == 2026])
    hist = defaultdict(int)
    for o in nat:
        if o["year"] <= 2025:
            for s in o["open"]:
                hist[s] += 1
    rng = np.random.default_rng(11)
    noise = {i: float(rng.random()) for i in universe}
    traffic = ({i: trains.get(segs[i]["edge"], 0) for i in universe}, "trains per day")
    length = ({i: edge_len[segs[i]["edge"]] for i in universe}, "length of the section between stations")
    rule = ({i: A[i]["rule"] for i in universe}, "weighted step-1 numbers")

    # main: all years, terrain-only scan (its inputs have no incident history)
    all_methods = {
        "Railsight": ({i: T[i]["risk"] for i in universe}, "terrain only, no history"),
        "Rule, no model": rule,
        "Traffic": traffic,
        "Section length": length,
        "Random": (noise, "expected: k"),
    }
    all_tab, all_rows = table(allyrs, all_methods)
    # secondary: out-of-time 2026, full scan with 2024-25 history
    test_methods = {
        "Railsight": ({i: A[i]["risk"] for i in universe}, "full cascade"),
        "Text step only": ({i: A[i].get("risk_text", A[i]["risk"]) for i in universe}, "no vision step"),
        "Rule, no model": rule,
        "Incident history": ({i: hist[i] for i in universe}, "2024-25 incidents on the section"),
        "Traffic": traffic,
    }
    test_tab, test_rows = table(test, test_methods)
    ranked = sorted(universe, key=lambda i: -A[i]["risk"])
    pos = {i: r / len(ranked) for r, i in enumerate(ranked)}
    rankedT = sorted(universe, key=lambda i: -T[i]["risk"])
    posT = {i: r / len(rankedT) for r, i in enumerate(rankedT)}
    listing = lambda incs, p: [{"id": str(n), "date": o["start"][:10], "section": o["section"], "cls": o["cls"],
                                "best_pct": round(min(p[s] for s in o["open"]), 3)} for n, o in enumerate(incs)]
    out = {"k": 0.10, "ks": KS, "universe": len(universe),
           "all_n": len(allyrs), "all_rows": all_rows, "all_table": all_tab, "all_list": listing(allyrs, posT),
           "test_n": len(test), "test_years": "2026 (Jan-Oct)", "rows": test_rows, "test_table": test_tab,
           "tests": listing(test, pos)}
    dump(out, WORK / "eval.json", indent=1)
    print(json.dumps({"all_n": out["all_n"], "all_table": all_tab, "test_n": out["test_n"], "test_table": test_tab}, indent=1))


if __name__ == "__main__":
    main()
