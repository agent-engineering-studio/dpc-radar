// The answers of demo/api.py, as the page reads them.

export type LayerKey = "now" | "1" | "3" | "6" | "12" | "24";

export type Failed = { error: string };

export type Layer = {
  key: LayerKey;
  product: string;
  time: string;
  unit: "mm" | "mm/h";
  hours: number | null;
  source: string;
  quality: { verdict: string | null; trusted_share: number; masked_pixels: number };
  image: string;
  bounds: [[number, number], [number, number]];
  legend: { from: number; colour: string }[];
  python: string;
};

export type Film = {
  key: LayerKey;
  product: string;
  step_minutes: number;
  frames: { time: string; image: string }[];
};

export type Moving = { kmh: number; towards_degrees: number } | null;

export type ZoneDay = {
  day: string;
  zone: string;
  level: number;
  colour: "none" | "yellow" | "orange" | "red";
  hydrogeological: string;
  hydraulic: string;
  thunderstorms: string;
};

export type AreaRain = {
  hours: number;
  time?: string;
  max_mm: number | null;
  max_lon?: number | null;
  max_lat?: number | null;
  mean_mm?: number | null;
  p95_mm?: number | null;
  km2_over?: Record<string, number>;
  measured_share?: number;
  colour?: string | null;
  error?: string;
};

export type PointRain = {
  hours: number;
  mm?: number | null;
  measured?: boolean;
  colour?: string | null;
  product?: string;
  time?: string;
  verdict?: string | null;
  rarity?: { return_period_years: number | null; at_least: boolean; percentile: number | null } | null;
  error?: string;
};

export type PointData = {
  lon: number;
  lat: number;
  now: { mm_h: number | null; measured: boolean; colour: string | null; time: string } | Failed;
  rain: PointRain[];
  zone:
    | { name: string; shown: string; bulletin: string; issued: string; days: ZoneDay[]; rain: AreaRain[] }
    | Failed
    | null;
  reliability: {
    score: number | null;
    reliable: boolean | null;
    beam_height_m: number | null;
    nearest_radar: string | null;
    distance_km: number | null;
  };
  radars:
    | {
        known: boolean;
        time: string | null;
        radars: { name: string; band: string; distance_km: number; on: boolean | null }[];
      }
    | Failed;
  python: string;
};

export type AheadData = {
  issued: string;
  steps: { minutes: number; forecast_mm: number | null }[];
  chance: { mm: number; minutes: number; probability: number | null };
  moving: Moving;
  python: string;
};

export type StormProps = {
  kind: "storm";
  lon: number;
  lat: number;
  area_km2: number;
  peak_mm_h: number;
  colour: string | null;
  peak_lon: number;
  peak_lat: number;
  mean_mm_h: number;
  moving: Moving;
};

export type RadarProps = {
  kind: "radar";
  name: string;
  code: string | null;
  band: string | null;
  range_km: number | null;
  altitude_m: number | null;
  operator: string | null;
  on: boolean | null;
  in_mosaic: boolean;
  lon: number;
  lat: number;
};

export type RadarRain = { name: string; range_km: number; rain: AreaRain[] };

export type Selection =
  | { kind: "point"; lon: number; lat: number }
  | { kind: "storm"; lon: number; lat: number; storm: StormProps }
  | { kind: "radar"; lon: number; lat: number; radar: RadarProps };

export type Collection<P> = {
  type: "FeatureCollection";
  features: { type: "Feature"; geometry: GeoJSON.Geometry; properties: P }[];
  [more: string]: unknown;
};
