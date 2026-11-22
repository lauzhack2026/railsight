export type Hazard = "tree" | "rock" | "slide" | "debris" | "flood" | "none";

export interface Features {
  open_m: number;
  tunnel_only?: boolean;
  canopy_0_15?: number;
  canopy_15_35?: number;
  treeline_share?: number;
  tree_h?: number;
  steep30_150?: number;
  steep40_150?: number;
  steep35_50?: number;
  rock_60?: number;
  slide_60?: number;
  debris_60?: number;
  flood_60?: number;
  hot?: [number, number];
}

export interface Assessment {
  gate: "tunnel" | "quiet" | "text" | "failed";
  risk: number;
  rule: number;
  hazard?: Hazard;
  confidence?: "low" | "medium" | "high";
  why?: string;
  look?: boolean;
  risk_text?: number;
  usd?: number;
  vision?: { verdict: "confirmed" | "rejected" | "unclear"; risk: number; seen: string; usd?: number; tokens?: number };
}

export interface Detail {
  from: string;
  to: string;
  k: number;
  n: number;
  op: string;
  len: number;
  tunnel: number;
  trains: number;
  pct: number; // risk percentile rank (0 = highest)
  f: Features;
  a: Assessment;
  inc: number[];
  mask?: [number, number, number, number]; // lon0, lat0, lon1, lat1 of the step-1 overlay
}

export interface Incident {
  id: string;
  date: string;
  year: number;
  cls: string | null;
  reason: string;
  section: string;
  dur: number | null;
}

export interface EvalRow {
  name: string;
  note: string;
  share: number;
  lo: number;
  hi: number;
}

export interface Summary {
  scan: { segments: number; km: number; open_km: number; tunnel_km: number; tiles: number; mb: number; ms_per_segment: number; minutes_fetch: number };
  cascade: { tunnel: number; quiet: number; text: number; vision: number; text_calls: number; vision_calls: number; km_text: number; km_vision: number };
  cost: {
    text_in: number; text_out: number; vision_in: number; vision_out: number;
    usd_text: number; usd_vision: number; usd_total: number; usd_per_100km: number;
    naive_usd_per_100km: number; naive_sample: number; naive_model: string; ratio: number;
    text_s: number; vision_s: number; model: string;
  };
  eval: {
    k: number; ks: number[]; universe: number;
    test_n: number; test_years: string; rows: EvalRow[]; test_table: Record<string, Record<string, [number, number, number]>>;
    all_n: number; all_rows: EvalRow[]; all_table: Record<string, Record<string, [number, number, number]>>;
    all_list: { id: string; date: string; section: string; cls: string; best_pct: number }[];
    tests: { id: string; date: string; section: string; cls: string; best_pct: number }[];
    note: string;
  };
  top: string[];
}
