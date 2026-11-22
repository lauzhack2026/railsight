import type * as maplibregl from "maplibre-gl";
import type { Map as LibreMap, StyleSpecification } from "maplibre-gl";

const LIGHT = "https://vectortiles.geo.admin.ch/styles/ch.swisstopo.lightbasemap.vt/style.json";
const HIDDEN = /public_transport|railway|^place_country$|^poi_rank2$/;
const AERIAL = "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg";

export const RISK_RAMP: [number, string][] = [
  [0, "#c3c9d1"],
  [25, "#d9c79a"],
  [45, "#f0b45a"],
  [62, "#ea7f37"],
  [78, "#d2452b"],
  [92, "#8f1d2c"],
];

export function riskColor(r: number): string {
  for (let i = RISK_RAMP.length - 1; i >= 0; i--) if (r >= RISK_RAMP[i][0]) {
    const [r0, c0] = RISK_RAMP[i];
    const next = RISK_RAMP[i + 1];
    if (!next) return c0;
    const t = (r - r0) / (next[0] - r0);
    return mix(c0, next[1], t);
  }
  return RISK_RAMP[0][1];
}
function mix(a: string, b: string, t: number) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return "#" + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

export async function baseStyle(): Promise<StyleSpecification> {
  const style = (await fetch(LIGHT).then((r) => r.json())) as StyleSpecification;
  style.layers = style.layers.map((l) => {
    if (HIDDEN.test(l.id)) return { ...l, layout: { ...((l as { layout?: object }).layout ?? {}), visibility: "none" } } as typeof l;
    if (l.id === "hillshade_grey") {
      const paint = { ...((l as { paint?: Record<string, unknown> }).paint ?? {}) };
      if (l.type === "raster") paint["raster-opacity"] = 0.45;
      if (l.type === "fill") paint["fill-opacity"] = 0.5;
      return { ...l, paint } as typeof l;
    }
    return l;
  });
  return style;
}

const colorExpr = ["interpolate", ["linear"], ["get", "r"], ...RISK_RAMP.flat()] as unknown as maplibregl.ExpressionSpecification;

export const HAZARD_GROUP: Record<string, string[]> = {
  all: ["t", "r", "s", "d", "f", "n"],
  tree: ["t"],
  slope: ["r", "s", "d"],
  flood: ["f"],
};

export function addLayers(map: LibreMap, data: GeoJSON.FeatureCollection) {
  // aerial imagery sits under the track, above the base map's labels' fills
  const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
  map.addSource("aerial", { type: "raster", tiles: [AERIAL], tileSize: 256, minzoom: 9, maxzoom: 19, bounds: [5.9, 45.8, 10.5, 47.9], attribution: "© swisstopo SWISSIMAGE" });
  map.addLayer({ id: "aerial", type: "raster", source: "aerial", layout: { visibility: "none" }, paint: { "raster-opacity": 1 } }, firstSymbol);
  map.addSource("seg", { type: "geojson", data, promoteId: "id" });
  map.addLayer({
    id: "tunnel", type: "line", source: "seg", filter: ["==", ["get", "g"], "T"],
    paint: { "line-color": "#9aa2ad", "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.8, 13, 2], "line-dasharray": [1.5, 2], "line-opacity": 0.8 },
  });
  map.addLayer({
    id: "casing", type: "line", source: "seg", filter: ["!=", ["get", "g"], "T"],
    layout: { "line-cap": "round", "line-join": "round", "line-sort-key": ["get", "r"] },
    paint: {
      "line-color": "#ffffff",
      "line-width": ["interpolate", ["linear"], ["zoom"], 7, ["+", 2.4, ["/", ["get", "r"], 33]], 11, ["+", 4, ["/", ["get", "r"], 22]], 15, 9],
      "line-opacity": 0.9,
    },
  });
  map.addLayer({
    id: "track", type: "line", source: "seg", filter: ["!=", ["get", "g"], "T"],
    layout: { "line-cap": "round", "line-join": "round", "line-sort-key": ["get", "r"] },
    paint: {
      "line-color": colorExpr,
      "line-width": ["interpolate", ["linear"], ["zoom"], 7, ["+", 1.1, ["/", ["get", "r"], 36]], 11, ["+", 2.2, ["/", ["get", "r"], 25]], 15, 6],
      "line-opacity-transition": { duration: 300 },
    },
  });
  map.addLayer({
    id: "sel-halo", type: "line", source: "seg", filter: ["==", ["get", "id"], ""],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#111418", "line-width": ["interpolate", ["linear"], ["zoom"], 7, 7, 13, 10, 16, 9], "line-opacity": 0.8 },
  });
  map.addLayer({
    id: "sel", type: "line", source: "seg", filter: ["==", ["get", "id"], ""],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": colorExpr, "line-width": ["interpolate", ["linear"], ["zoom"], 7, 3.5, 13, 6, 16, 4.5] },
  });
  map.addSource("hot", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "hot", type: "circle", source: "hot",
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 6, 16, 26], "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 },
  });
}

export function setFilter(map: LibreMap, group: string) {
  const codes = HAZARD_GROUP[group] ?? HAZARD_GROUP.all;
  const op = group === "all" ? 1 : ["case", ["in", ["get", "h"], ["literal", codes]], 1, 0.12];
  map.setPaintProperty("track", "line-opacity", op as never);
  map.setPaintProperty("casing", "line-opacity", (group === "all" ? 0.9 : ["case", ["in", ["get", "h"], ["literal", codes]], 0.9, 0]) as never);
}

const BUSY_ON_AERIAL = ["contour_line", "contour_line_blue", "contour_line_pt", "spot_elevation"];
export function setAerial(map: LibreMap, on: boolean) {
  map.setLayoutProperty("aerial", "visibility", on ? "visible" : "none");
  for (const id of BUSY_ON_AERIAL) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "none" : "visible");
}

export function setMask(map: LibreMap, id: string | null, box?: [number, number, number, number]) {
  if (map.getLayer("mask")) map.removeLayer("mask");
  if (map.getSource("mask")) map.removeSource("mask");
  if (!id || !box) return;
  const [lon0, lat0, lon1, lat1] = box;
  map.addSource("mask", { type: "image", url: `/masks/${id}.png`, coordinates: [[lon0, lat1], [lon1, lat1], [lon1, lat0], [lon0, lat0]] });
  map.addLayer({ id: "mask", type: "raster", source: "mask", paint: { "raster-opacity": 0.85, "raster-fade-duration": 200 } }, "tunnel");
}
