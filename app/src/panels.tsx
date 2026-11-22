import { useEffect, useState } from "react";
import { riskColor } from "./map";
import type { Detail, EvalRow, Incident, Summary } from "./types";

const HZ: Record<string, string> = { tree: "Trees", rock: "Rockfall", slide: "Landslide", debris: "Debris flow", flood: "Flood", none: "No dominant hazard" };
const CLS: Record<string, string> = { tree: "Tree / storm", slope: "Slope", water: "Water", obstacle: "Obstacle" };
const level = (r: number) => (r >= 80 ? "Very high" : r >= 60 ? "High" : r >= 40 ? "Moderate" : r >= 20 ? "Low" : "Negligible");
const fmt = (n: number) => n.toLocaleString("en");
const usd = (x: number) => (x >= 1 ? `$${x.toFixed(2)}` : x >= 0.01 ? `$${x.toFixed(3)}` : `$${x.toFixed(4)}`);
const pc = (x: number, d = 0) => `${(100 * x).toFixed(d)}%`;

export function Overview({ summary: s, details, top, inView, onPick }: { summary: Summary; details: Record<string, Detail>; top: string[]; inView: boolean; onPick: (id: string) => void }) {
  const c = s.cascade;
  const steps = [
    { n: s.scan.segments, label: `segments of ~1 km: ${fmt(Math.round(s.scan.km))} km of standard-gauge track`, tone: "base" },
    { n: c.tunnel, label: "in tunnels: skipped", tone: "skip" },
    { n: c.quiet, label: "nothing near the track: decided without any model", tone: "skip" },
    { n: c.text, label: `read as numbers by a small language model (${c.text_calls} calls)`, tone: "text" },
    { n: c.vision, label: "looked at by a vision model, one 320 m crop each", tone: "vision" },
  ];
  return (
    <div className="stack">
      <section>
        <h2>How it works</h2>
        <p className="muted">
          Classical image processing turns aerial imagery, the national vegetation height model and official hazard maps into a
          dozen numbers per kilometre. A small language model rates those numbers. A vision model looks at a picture only
          where the numbers leave a real doubt.
        </p>
        <div className="funnel">
          {steps.map((x, i) => (
            <div key={i} className={`funnel-row ${x.tone}`}>
              <div className="funnel-bar"><span style={{ width: `${Math.max(1.5, (100 * x.n) / s.scan.segments)}%` }} /></div>
              <div className="funnel-n">{fmt(x.n)}</div>
              <div className="funnel-label">{x.label}</div>
            </div>
          ))}
        </div>
      </section>
      <section>
        <h2>{inView ? "Highest-risk stretches in view" : "Highest-risk stretches"}</h2>
        <ol className="toplist">
          {dedupe(top, details).slice(0, 9).map((id) => {
            const d = details[id];
            if (!d) return null;
            return (
              <li key={id} onClick={() => onPick(id)}>
                <span className="pill" style={{ background: riskColor(d.a.risk) }}>{d.a.risk}</span>
                <span className="top-name">{clean(d.from)} – {clean(d.to)}</span>
                <span className="top-hz">{HZ[d.a.hazard ?? "none"]}{d.a.vision ? " · seen" : ""}</span>
              </li>
            );
          })}
        </ol>
        <p className="hint">Click any stretch of track on the map to see what the scan saw there.</p>
      </section>
    </div>
  );
}

function dedupe(top: string[], details: Record<string, Detail>) {
  const seen = new Set<string>();
  return top.filter((id) => {
    const d = details[id];
    if (!d) return false;
    const key = [d.from, d.to].sort().join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const clean = (s: string) => s.replace(/\s*\((Spw|Abzw|Abzw\.|dira|c bin|bif)\)\s*/g, "").trim();

function Bar({ label, v, hint }: { label: string; v?: number; hint?: string }) {
  const x = v ?? 0;
  return (
    <div className="bar" title={hint}>
      <span className="bar-label">{label}</span>
      <span className="bar-track"><span className="bar-fill" style={{ width: `${Math.min(100, 100 * x)}%` }} /></span>
      <span className="bar-v">{v === undefined ? "–" : pc(x)}</span>
    </div>
  );
}

export function SegmentView({ id, d, incidents, onClose }: { id: string; d: Detail; incidents: Incident[]; onClose: () => void }) {
  const a = d.a, f = d.f;
  const inc = d.inc.map((i) => incidents[i]).filter(Boolean).sort((x, y) => y.date.localeCompare(x.date));
  const visionTone = a.vision?.verdict === "confirmed" ? "ok" : a.vision?.verdict === "rejected" ? "no" : "unk";
  return (
    <div className="stack">
      <section className="seg-head">
        <div>
          <h1>{clean(d.from)} – {clean(d.to)}</h1>
          <div className="muted small">
            km {d.k + 1} of {d.n} · {d.op.replace(" CFF FFS", "")} · {d.trains ? `${d.trains} trains a day` : "no scheduled trains in the sample day"}
            {d.tunnel > 0.02 && d.tunnel < 1 ? ` · ${pc(d.tunnel)} in tunnel` : ""}
          </div>
        </div>
        <button className="close" onClick={onClose} aria-label="Close">×</button>
      </section>

      <section className="risk-block">
        <div className="risk-num" style={{ color: riskColor(a.risk) }}>{a.gate === "tunnel" ? "–" : a.risk}</div>
        <div className="risk-meta">
          <div className="risk-level">{a.gate === "tunnel" ? "In tunnel" : level(a.risk)}{a.hazard && a.hazard !== "none" ? ` · ${HZ[a.hazard]}` : ""}</div>
          <div className="muted small">
            {a.gate === "tunnel" ? "Not exposed to lineside hazards" : `Among the riskiest ${Math.max(1, Math.ceil(100 * d.pct))}% of the network`}
            {a.confidence ? ` · ${a.confidence} confidence` : ""}
          </div>
        </div>
      </section>

      <section>
        <h3>The three steps</h3>
        <div className="steps">
          <div className="step">
            <div className="step-k">1</div>
            <div className="step-b">
              <div className="step-t">Measured, no model</div>
              <div className="muted small">
                {a.gate === "tunnel" ? "The track runs in a tunnel here." : `${f.open_m} m of open track · on the map, dark green: trees tall enough to reach the track`}
              </div>
            </div>
          </div>
          <div className="step">
            <div className="step-k">2</div>
            <div className="step-b">
              <div className="step-t">Small language model</div>
              {a.gate === "text" ? (
                <div className="quote">“{a.why}”{a.risk_text !== undefined && a.risk_text !== a.risk ? <span className="muted"> · rated {a.risk_text} before the image</span> : null}</div>
              ) : (
                <div className="muted small">Skipped: {a.gate === "tunnel" ? "nothing to see in a tunnel." : "no trees, steep ground or hazard areas near the track."}</div>
              )}
            </div>
          </div>
          <div className="step">
            <div className="step-k">3</div>
            <div className="step-b">
              <div className="step-t">Vision model</div>
              {a.vision ? (
                <>
                  <div className={`verdict ${visionTone}`}>{a.vision.verdict === "confirmed" ? "Confirmed" : a.vision.verdict === "rejected" ? "Rejected" : "Unclear"} · {a.vision.risk}/100</div>
                  <div className="quote">“{a.vision.seen}”</div>
                  <img className="crop" src={`/crops/${id}.jpg`} alt="Aerial crop sent to the vision model" />
                  <div className="muted tiny">The 320 m crop the model saw. Magenta: the track. © swisstopo SWISSIMAGE</div>
                </>
              ) : (
                <div className="muted small">{a.gate === "text" ? "Not needed: the numbers were clear enough." : "Not needed."}</div>
              )}
            </div>
          </div>
        </div>
      </section>

      {a.gate !== "tunnel" && (
        <div className="cost-line">
          <span>What this kilometre cost</span>
          <b>{a.gate === "quiet" ? "$0 · no model" : `$${((a.usd ?? 0) + (a.vision?.usd ?? 0)).toFixed(6)}`}</b>
        </div>
      )}

      <LiveScan id={id} />

      {a.gate !== "tunnel" && (
        <section>
          <h3>What step 1 measured</h3>
          <div className="bars">
            <div className="bars-group">Trees (vegetation height model)</div>
            <Bar label="Trees ≥ 5 m, 4–15 m away" v={f.canopy_0_15} />
            <Bar label="Trees ≥ 15 m, 15–35 m away" v={f.canopy_15_35} />
            <Bar label="Track tall trees can reach" v={f.treeline_share} />
            <div className="bar">
              <span className="bar-label">Height of the tall trees</span>
              <span className="bar-track"><span className="bar-fill" style={{ width: `${Math.min(100, (100 * (f.tree_h ?? 0)) / 40)}%` }} /></span>
              <span className="bar-v">{f.tree_h ?? 0} m</span>
            </div>
            <div className="bars-group">Terrain within 150 m</div>
            <Bar label="Steeper than 30°" v={f.steep30_150} />
            <Bar label="Steeper than 40°" v={f.steep40_150} />
            <div className="bars-group">Official hazard areas within 60 m</div>
            <Bar label="Rockfall" v={f.rock_60} />
            <Bar label="Shallow landslide" v={f.slide_60} />
            <Bar label="Debris flow" v={f.debris_60} />
            <Bar label="100-year flood" v={f.flood_60} />
          </div>
        </section>
      )}

      <section>
        <h3>Incidents on this section</h3>
        {inc.length === 0 ? (
          <div className="muted small">No disruption recorded between these stations, March 2024 – October 2026.</div>
        ) : (
          <ul className="incidents">
            {inc.slice(0, 8).map((x) => (
              <li key={x.id} className={x.cls ? "nat" : ""}>
                <span className="inc-date">{x.date}</span>
                <span className="inc-what">{x.reason}{x.cls ? <span className="tag">{CLS[x.cls]}</span> : null}</span>
              </li>
            ))}
            {inc.length > 8 && <li className="muted small">and {inc.length - 8} more</li>}
          </ul>
        )}
      </section>
    </div>
  );
}

export function Frugality({ s }: { s: Summary }) {
  const c = s.cascade, k = s.cost;
  const total = s.scan.segments;
  const avoided = c.tunnel + c.quiet;
  return (
    <div className="stack">
      <section>
        <h2>Cost of the whole scan</h2>
        <div className="big-compare">
          <div><b>{usd(k.usd_per_100km)}</b><span>per 100 km with Railsight</span></div>
          <div className="vs">vs</div>
          <div><b>{usd(k.naive_usd_per_100km)}</b><span>per 100 km sending every km to {k.naive_model}</span></div>
        </div>
        <p className="muted small">
          {Math.round(k.ratio)}× cheaper. The naive figure is measured, not estimated: {k.naive_sample} segments were sent to {k.naive_model} as
          one 1,024 px aerial image each, and its tokens priced at list price.
        </p>
      </section>
      <section>
        <h3>Where the model calls go</h3>
        <table className="tbl">
          <tbody>
            <tr><td>Segments</td><td className="num">{fmt(total)}</td><td className="num muted">100%</td></tr>
            <tr><td>Decided without a model (tunnel or nothing nearby)</td><td className="num">{fmt(avoided)}</td><td className="num muted">{pc(avoided / total)}</td></tr>
            <tr><td>Read by {k.model} as numbers, 30 per call</td><td className="num">{fmt(c.text)}</td><td className="num muted">{pc(c.text / total)}</td></tr>
            <tr><td>Image crop sent to the vision model</td><td className="num">{fmt(c.vision)}</td><td className="num muted">{pc(c.vision / total, 1)}</td></tr>
          </tbody>
        </table>
        <p className="muted small">{fmt(c.text_calls + c.vision_calls)} model calls in total, instead of {fmt(total)} image calls.</p>
      </section>
      <section>
        <h3>Tokens and money</h3>
        <table className="tbl">
          <thead><tr><td></td><td className="num">input</td><td className="num">output</td><td className="num">cost</td></tr></thead>
          <tbody>
            <tr><td>Text step</td><td className="num">{fmt(k.text_in)}</td><td className="num">{fmt(k.text_out)}</td><td className="num">{usd(k.usd_text)}</td></tr>
            <tr><td>Vision step</td><td className="num">{fmt(k.vision_in)}</td><td className="num">{fmt(k.vision_out)}</td><td className="num">{usd(k.usd_vision)}</td></tr>
            <tr className="sum"><td>Whole network</td><td className="num">{fmt(k.text_in + k.vision_in)}</td><td className="num">{fmt(k.text_out + k.vision_out)}</td><td className="num">{usd(k.usd_total)}</td></tr>
          </tbody>
        </table>
      </section>
      <section>
        <h3>Time and data</h3>
        <table className="tbl">
          <tbody>
            <tr><td>Step 1, image processing per segment (one CPU core)</td><td className="num">{s.scan.ms_per_segment} ms</td></tr>
            <tr><td>Text call, 30 segments</td><td className="num">{k.text_s.toFixed(1)} s</td></tr>
            <tr><td>Vision call, one crop</td><td className="num">{k.vision_s.toFixed(1)} s</td></tr>
            <tr><td>Map tiles downloaded once and cached</td><td className="num">{fmt(s.scan.tiles)} · {fmt(Math.round(s.scan.mb))} MB</td></tr>
          </tbody>
        </table>
        <p className="muted small">Only tiles touching a corridor around open track are fetched: 40 m for trees, 150 m for slopes, 60 m for hazard maps.</p>
      </section>
    </div>
  );
}

function EvalBars({ rows, k }: { rows: EvalRow[]; k: number }) {
  return (
    <div className="ebars">
      {rows.map((r) => (
        <div key={r.name} className={`ebar ${r.name === "Railsight" ? "main" : ""}`}>
          <div className="ebar-name">{r.name}<span className="muted tiny">{r.note}</span></div>
          <div className="ebar-track">
            <span className="ebar-ci" style={{ left: `${100 * r.lo}%`, width: `${100 * Math.max(0.004, r.hi - r.lo)}%` }} />
            <span className="ebar-fill" style={{ width: `${100 * r.share}%` }} />
            <span className="ebar-k" style={{ left: `${100 * k}%` }} />
          </div>
          <div className="ebar-v">{pc(r.share)}</div>
        </div>
      ))}
    </div>
  );
}

function KTable({ tab }: { tab: Record<string, Record<string, [number, number, number]>> }) {
  const ks = ["5", "10", "20"];
  return (
    <table className="tbl ktbl">
      <thead><tr><td>Share of incidents in the top</td>{ks.map((k) => <td key={k} className="num">{k}% of track</td>)}</tr></thead>
      <tbody>
        {Object.entries(tab).map(([name, v]) => (
          <tr key={name} className={name === "Railsight" ? "main" : ""}>
            <td>{name}</td>
            {ks.map((k) => (
              <td key={k} className="num">{pc(v[k][0])}<span className="ci">{pc(v[k][1])}–{pc(v[k][2])}</span></td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Evaluation({ s }: { s: Summary }) {
  const e = s.eval;
  const main = e.all_rows.find((r) => r.name === "Railsight");
  const lift = main ? main.share / e.k : 0;
  const better = main ? main.lo > e.k : false;
  return (
    <div className="stack">
      <section>
        <h2>Does it work?</h2>
        <p className="muted">
          Every natural-hazard disruption SBB announced since March 2024 (fallen trees, storm damage, obstacles on the track,
          slides, floods; n = {e.all_n}), against a scan that never saw any incident: the text step read only the terrain
          numbers. Did the incidents happen on the track it ranks riskiest?
        </p>
        {main && (
          <div className="headline">
            <b>{pc(main.share)}</b>
            <span>
              of the incidents fell on the {pc(e.k)} of track ranked riskiest
              {better ? `: ${lift.toFixed(1)}× what a random ranking catches` : lift > 1.2 ? `: ${lift.toFixed(1)}× random, but the interval includes random` : ": no better than a random ranking"} (95% interval {pc(main.lo)}–{pc(main.hi)}).
            </span>
          </div>
        )}
        <EvalBars rows={e.all_rows} k={e.k} />
        <KTable tab={e.all_table} />
        <p className="muted tiny">Each incident is spread evenly over the segments of its station-to-station section. Intervals: 95% bootstrap over incidents. Tick on the bars: what a random ranking catches.</p>
      </section>
      <section>
        <h3>Out of time: {e.test_years} (n = {e.test_n})</h3>
        <p className="muted small">The full scan, which saw the incidents of 2024–25 as history, against the incidents of 2026 only. Too few to conclude anything.</p>
        <EvalBars rows={e.rows} k={e.k} />
        <ul className="incidents">
          {e.tests.map((t) => (
            <li key={t.id}>
              <span className="inc-date">{t.date}</span>
              <span className="inc-what">{t.section}<span className="tag">{CLS[t.cls] ?? t.cls}</span></span>
              <span className="inc-rank">top {pc(t.best_pct, t.best_pct < 0.1 ? 1 : 0)}</span>
            </li>
          ))}
        </ul>
        <p className="muted tiny">top x%: the best-ranked segment of the incident's section.</p>
      </section>
      <section className="note">
        <h3>Honestly</h3>
        <p className="small">{e.note}</p>
      </section>
    </div>
  );
}

type LiveStep = { step: number; name?: string; ms?: number; tiles?: number; in?: number; out?: number; usd?: number; skipped?: string };
type Live = { steps: LiveStep[]; result: { risk: number; why?: string; vision?: { verdict: string; seen: string } }; error?: string };

let liveAvailable: Promise<boolean> | null = null;
/** The live re-scan needs pipeline/server.py; the static deployment has no model calls and hides it. */
const probeLive = () => (liveAvailable ??= fetch("/api/health").then((r) => r.ok).catch(() => false));

function LiveScan({ id }: { id: string }) {
  const [available, setAvailable] = useState(false);
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [live, setLive] = useState<Live | null>(null);
  useEffect(() => { probeLive().then(setAvailable); }, []);
  useEffect(() => { setState("idle"); setLive(null); }, [id]);
  if (!available) return null;
  async function run() {
    setState("busy");
    try {
      const r = await fetch(`/api/scan/${id}`);
      const j = (await r.json()) as Live;
      if (!r.ok || j.error) throw new Error(j.error);
      setLive(j); setState("done");
    } catch {
      setState("error");
    }
  }
  const total = live ? live.steps.reduce((x, s) => x + (s.usd ?? 0), 0) : 0;
  const ms = live ? live.steps.reduce((x, s) => x + (s.ms ?? 0), 0) : 0;
  return (
    <section className="live">
      <div className="live-head">
        <div>
          <div className="step-t">Run it again, live</div>
          <div className="muted small">Same three steps on this kilometre, now.</div>
        </div>
        <button className="btn" onClick={run} disabled={state === "busy"}>{state === "busy" ? "Scanning…" : state === "done" ? "Again" : "Re-scan"}</button>
      </div>
      {state === "error" && <div className="muted small">The live scan failed; the stored result above stands.</div>}
      {live && (
        <table className="tbl live-tbl">
          <tbody>
            {live.steps.map((s) => (
              <tr key={s.step}>
                <td>{s.step}. {s.name ?? (s.step === 2 ? "Small language model" : "Vision model")}</td>
                {s.skipped ? (
                  <td className="num muted" colSpan={2}>skipped: {s.skipped}</td>
                ) : s.step === 1 ? (
                  <><td className="num">{s.ms} ms</td><td className="num muted">{s.tiles} cached tiles · $0</td></>
                ) : (
                  <><td className="num">{((s.ms ?? 0) / 1000).toFixed(1)} s</td><td className="num muted">{fmt((s.in ?? 0) + (s.out ?? 0))} tokens · ${(s.usd ?? 0).toFixed(5)}</td></>
                )}
              </tr>
            ))}
            <tr className="sum"><td>Result: {live.result.risk}/100</td><td className="num">{(ms / 1000).toFixed(1)} s</td><td className="num">${total.toFixed(5)}</td></tr>
          </tbody>
        </table>
      )}
      {live?.result.why && <div className="quote small">“{live.result.why}”{live.result.vision ? ` · vision: ${live.result.vision.verdict}, “${live.result.vision.seen}”` : ""}</div>}
    </section>
  );
}
