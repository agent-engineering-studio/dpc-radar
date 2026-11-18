import type { Failed, ZoneDay } from "./types";

/** The colours of the bulletin: no alert, yellow, orange, red. */
export const ALERT: Record<ZoneDay["colour"], string> = {
  none: "#5b6472",
  yellow: "#f2c200",
  orange: "#f07f00",
  red: "#d7191c",
};

export const failed = (value: unknown): value is Failed =>
  typeof value === "object" && value !== null && "error" in value;

export async function ask<T>(path: string, signal?: AbortSignal): Promise<T> {
  const answer = await fetch(path, { signal, cache: "no-store" });
  const body = await answer.json().catch(() => ({ error: `HTTP ${answer.status}` }));
  if (!answer.ok) throw new Error(body.error ?? `HTTP ${answer.status}`);
  return body as T;
}

/** "2026-10-10T06:00Z", as the API writes a time, as a Date. */
export const when = (stamp: string) => new Date(stamp.replace(/Z$/, ":00Z"));

const CLOCK = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Rome",
});
/** The time in Italy, which is where the rain is. */
export const clock = (stamp: string) => CLOCK.format(when(stamp));

export const place = (lon: number, lat: number) =>
  `${Math.abs(lat).toFixed(3)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(3)}° ${lon >= 0 ? "E" : "W"}`;

/** How far, in km, and towards where, in degrees from north, the second point is from the first. */
export function away(lon1: number, lat1: number, lon2: number, lat2: number): { km: number; degrees: number } {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  const y = Math.sin(dLon) * Math.cos(lat2 * rad);
  const x = Math.cos(lat1 * rad) * Math.sin(lat2 * rad) - Math.sin(lat1 * rad) * Math.cos(lat2 * rad) * Math.cos(dLon);
  return { km: 2 * 6371 * Math.asin(Math.sqrt(a)), degrees: (Math.atan2(y, x) / rad + 360) % 360 };
}
