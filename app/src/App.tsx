import * as maplibregl from "maplibre-gl";
import type { Map as LibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { addLayers, baseStyle, riskColor, setAerial, setFilter, setMask } from "./map";
import { Evaluation, Frugality, Overview, SegmentView } from "./panels";
import type { Detail, Incident, Summary } from "./types";

const PRESETS: { id: string; label: string; bounds: [number, number, number, number] }[] = [
  { id: "ch", label: "Switzerland", bounds: [5.95, 45.82, 10.49, 47.81] },
  { id: "gotthard", label: "Gotthard", bounds: [8.52, 46.52, 8.82, 47.03] },
  { id: "loetschberg", label: "Lötschberg", bounds: [7.55, 46.28, 8.02, 46.72] },
  { id: "jura", label: "Jura", bounds: [6.75, 47.02, 7.35, 47.25] },
  { id: "mittelland", label: "Mittelland", bounds: [7.42, 46.93, 8.32, 47.42] },
];
const FILTERS = [
  { id: "all", label: "All hazards" },
  { id: "tree", label: "Trees" },
  { id: "slope", label: "Rock & slope" },
  { id: "flood", label: "Flood" },
];
type Tab = "scan" | "frugal" | "eval";

export function App() {
  const mapDiv = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const [details, setDetails] = useState<Record<string, Detail>>({});
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("scan");
  const [filter, setFilt] = useState("all");
  const [aerial, setAer] = useState(false);
  const [preset, setPreset] = useState("ch");
  const [hover, setHover] = useState<{ x: number; y: number; id: string } | null>(null);
  const geo = useRef<GeoJSON.FeatureCollection | null>(null);
  const centroids = useRef<Record<string, [number, number]>>({});
  const [inView, setInView] = useState<string[] | null>(null);

  useEffect(() => {
    let map: LibreMap | undefined;
    (async () => {
      const [style, seg, det, inc, sum] = await Promise.all([
        baseStyle(),
        fetch("/data/segments.json").then((r) => r.json()),
        fetch("/data/details.json").then((r) => r.json()),
        fetch("/data/incidents.json").then((r) => r.json()),
        fetch("/data/summary.json").then((r) => r.json()),
      ]);
      geo.current = seg;
      for (const f of (seg as GeoJSON.FeatureCollection).features) {
        if (f.properties!.g === "T") continue;
        const g = f.geometry as GeoJSON.MultiLineString;
        const pts = g.coordinates.flat();
        const m = pts[Math.floor(pts.length / 2)];
        if (m) centroids.current[String(f.properties!.id)] = [m[0], m[1]];
      }
      setDetails(det);
      setIncidents(inc);
      setSummary(sum);
      map = new maplibregl.Map({
        container: mapDiv.current!,
        style,
        bounds: PRESETS[0].bounds,
        fitBoundsOptions: { padding: { top: 40, bottom: 40, left: 380, right: 460 } },
        attributionControl: false,
        maxPitch: 0,
        dragRotate: false,
      });
      map.touchZoomRotate.disableRotation();
      map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "Track: BAV · Hazard layers: BAFU, swisstopo · Tunnels: © OpenStreetMap" }), "bottom-left");
      map.on("load", () => {
        addLayers(map!, seg);
        mapRef.current = map!;
        setReady(true);
        const h = decodeURIComponent(location.hash.slice(1));
        if (h && det[h]) setTimeout(() => select(h), 50);
      });
      map.on("mousemove", "track", (e) => {
        const f = e.features?.[0];
        map!.getCanvas().style.cursor = f ? "pointer" : "";
        if (f) setHover({ x: e.point.x, y: e.point.y, id: String(f.properties!.id) });
      });
      map.on("mouseleave", "track", () => {
        map!.getCanvas().style.cursor = "";
        setHover(null);
      });
      map.on("moveend", () => {
        const b = map!.getBounds();
        const z = map!.getZoom();
        if (z < 8.2) { setInView(null); return; }
        setInView(Object.keys(centroids.current).filter((id) => b.contains(centroids.current[id] as [number, number])));
      });
      map.on("click", "track", (e) => {
        const f = e.features?.[0];
        if (f) select(String(f.properties!.id));
      });
    })();
    return () => map?.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (ready && mapRef.current) setFilter(mapRef.current, filter);
  }, [filter, ready]);
  useEffect(() => {
    if (ready && mapRef.current) setAerial(mapRef.current, aerial);
  }, [aerial, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setFilter("sel", ["==", ["get", "id"], sel ?? ""]);
    map.setFilter("sel-halo", ["==", ["get", "id"], sel ?? ""]);
    const d = sel ? details[sel] : undefined;
    setMask(map, sel && d?.mask ? sel : null, d?.mask);
    const hot = d?.a.vision && d.f.hot ? [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: d.f.hot } }] : [];
    (map.getSource("hot") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features: hot } as GeoJSON.FeatureCollection);
  }, [sel, ready, details]);

  function select(id: string | null, fly = true) {
    setSel(id);
    history.replaceState(null, "", id ? `#${id}` : location.pathname);
    setTab("scan");
    const map = mapRef.current;
    if (!id || !map || !geo.current) return;
    const f = geo.current.features.find((x) => x.properties!.id === id);
    if (!f || !fly) return;
    const g = f.geometry as GeoJSON.LineString | GeoJSON.MultiLineString;
    const co = g.type === "LineString" ? g.coordinates : g.coordinates.flat();
    const b = co.reduce((bb, [x, y]) => [Math.min(bb[0], x), Math.min(bb[1], y), Math.max(bb[2], x), Math.max(bb[3], y)], [180, 90, -180, -90]);
    map.fitBounds(b as [number, number, number, number], { padding: { top: 80, bottom: 80, left: 400, right: 480 }, maxZoom: 15.2, duration: 1400 });
    setAer(true);
  }

  function goPreset(id: string) {
    setPreset(id);
    const p = PRESETS.find((x) => x.id === id)!;
    mapRef.current?.fitBounds(p.bounds, { padding: { top: 40, bottom: 40, left: 380, right: 460 }, duration: 1200 });
    if (id === "ch") setAer(false);
  }

  const hovered = hover ? details[hover.id] : undefined;
  const ranked = useMemo(() => {
    if (!inView) return summary?.top ?? [];
    return [...inView].sort((a, b) => (details[b]?.a.risk ?? 0) - (details[a]?.a.risk ?? 0)).slice(0, 40);
  }, [summary, inView, details]);

  return (
    <div className="app">
      <div ref={mapDiv} className="map" />

      <header className={`card brand ${sel ? "compact" : ""}`}>
        <div className="wordmark">
          <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden>
            <rect width="32" height="32" rx="8" fill="#1f2328" />
            <path d="M7 22 L14 15 L18 19 L25 10" stroke="#f0a43a" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>Railsight</span>
        </div>
        <p className="tagline">A frugal natural-hazard scan of the Swiss rail network from the air.</p>
        {summary && !sel && (
          <div className="kpis">
            <div><b>{Math.round(summary.scan.km).toLocaleString("en")}</b><span>km scanned</span></div>
            <div><b>${summary.cost.usd_total.toFixed(2)}</b><span>model cost, whole network</span></div>
            <div><b>{pct(summary.cascade.vision / summary.scan.segments)}</b><span>of segments seen by a vision model</span></div>
          </div>
        )}
        <div className="group">
          <div className="label">Region</div>
          <div className="seg-ctl">
            {PRESETS.map((p) => (
              <button key={p.id} className={preset === p.id ? "on" : ""} onClick={() => goPreset(p.id)}>{p.label}</button>
            ))}
          </div>
        </div>
        <div className="group hz">
          <div className="label">Hazard</div>
          <div className="seg-ctl">
            {FILTERS.map((f) => (
              <button key={f.id} className={filter === f.id ? "on" : ""} onClick={() => setFilt(f.id)}>{f.label}</button>
            ))}
          </div>
        </div>
        <div className="group row">
          <div className="legend">
            <div className="ramp" />
            <div className="ramp-labels"><span>negligible</span><span>moderate</span><span>very high</span></div>
          </div>
          <div className="seg-ctl small">
            <button className={!aerial ? "on" : ""} onClick={() => setAer(false)}>Map</button>
            <button className={aerial ? "on" : ""} onClick={() => setAer(true)}>Aerial</button>
          </div>
        </div>
        <div className="tunnel-key"><span className="dash" /> in tunnel, not scanned{sel ? <> · <span className="canopy-key" /> trees that can reach it</> : null}</div>
      </header>

      <aside className="panel">
        <nav className="tabs">
          <button className={tab === "scan" ? "on" : ""} onClick={() => setTab("scan")}>{sel ? "Segment" : "Scan"}</button>
          <button className={tab === "frugal" ? "on" : ""} onClick={() => setTab("frugal")}>Frugality</button>
          <button className={tab === "eval" ? "on" : ""} onClick={() => setTab("eval")}>Does it work?</button>
        </nav>
        <div className="panel-body">
          {!summary ? (
            <div className="loading">Loading the scan…</div>
          ) : tab === "scan" ? (
            sel && details[sel] ? (
              <SegmentView id={sel} d={details[sel]} incidents={incidents} onClose={() => { select(null); }} />
            ) : (
              <Overview summary={summary} details={details} top={ranked} inView={!!inView} onPick={(id) => select(id)} />
            )
          ) : tab === "frugal" ? (
            <Frugality s={summary} />
          ) : (
            <Evaluation s={summary} />
          )}
        </div>
      </aside>

      {hover && hovered && (
        <div className="tip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
          <span className="dot" style={{ background: riskColor(hovered.a.risk) }} />
          <span className="tip-name">{hovered.from} – {hovered.to}</span>
          <span className="tip-risk">{hovered.a.risk}</span>
        </div>
      )}
    </div>
  );
}

export const pct = (x: number, d = 0) => `${(100 * x).toFixed(d)}%`;
