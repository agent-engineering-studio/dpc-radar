// Where the map opens. A municipality that puts this page on its site gives its own
// coordinates, in the environment or in the address of the page; with none, Gioia del Colle,
// in Apulia.

import type { LayerKey } from "./types";

export type Start = {
  lat: number;
  lon: number;
  zoom: number;
  /** The name to show, such as "Comune di Melito di Porto Salvo". */
  place: string | null;
  /** True when the coordinates were given: the place gets a marker, and its card is open. */
  given: boolean;
  /** What the map shows when it opens: the rain of now, or of the last hours. */
  layer: LayerKey;
  /** One of the older frames of the film, 0 the oldest; null is the live map. */
  frame: number | null;
};

const LAYERS: LayerKey[] = ["now", "1", "3", "6", "12", "24"];

/** The layer and the frame asked for: the first valid one, whoever gave the coordinates. */
function view(sources: Asked[]): Pick<Start, "layer" | "frame"> {
  const layer = sources.map((one) => one.layer?.trim()).find((one) => LAYERS.includes(one as LayerKey));
  const frame = sources.map((one) => number(one.frame)).find((one) => one !== null && one >= 0 && one <= 12);
  return { layer: (layer as LayerKey) ?? "now", frame: frame === undefined || frame === null ? null : Math.round(frame) };
}

export type Asked = { lat?: string; lon?: string; zoom?: string; place?: string; layer?: string; frame?: string };

// Gioia del Colle (Bari), the centre of the town.
const DEFAULT = { lat: 40.7997, lon: 16.9236 };
// The town with the Murge around it: the storms that are coming are in the picture.
const DEFAULT_ZOOM = 10;
// A municipality and its neighbours.
const PLACE_ZOOM = 11;
export const MIN_ZOOM = 5;
export const MAX_ZOOM = 13;
// What the radar mosaic covers, with a margin: coordinates outside it are a mistake.
const AREA = { south: 35, north: 48, west: 6, east: 19.6 };

const number = (text: string | undefined) => {
  const value = text?.trim() ? Number(text.replace(",", ".")) : NaN;
  return Number.isFinite(value) ? value : null;
};

const inside = (lat: number | null, lon: number | null): lat is number =>
  lat !== null && lon !== null && lat >= AREA.south && lat <= AREA.north && lon >= AREA.west && lon <= AREA.east;

/** The start from what was asked: one source at a time, so a name never goes with the coordinates of another. */
export function startFrom(...sources: Asked[]): Start {
  for (const asked of sources) {
    const lat = number(asked.lat);
    const lon = number(asked.lon);
    if (!inside(lat, lon)) continue;
    const zoom = number(asked.zoom) ?? PLACE_ZOOM;
    return {
      lat,
      lon: lon as number,
      zoom: Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom)),
      place: asked.place?.trim().slice(0, 80) || null,
      given: true,
      ...view(sources),
    };
  }
  return { ...DEFAULT, zoom: DEFAULT_ZOOM, place: null, given: false, ...view(sources) };
}

export type Tiles = { url: string; attribution: string };

/** The background map: OpenStreetMap, or the tiles of whoever hosts the page. */
export const tilesFrom = (url: string | undefined, attribution: string | undefined): Tiles => ({
  url: url?.trim() || "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  attribution:
    attribution?.trim() ||
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
});
