import type { Failed, Moving, ZoneDay } from "./types";

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
const DAY = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "Europe/Rome",
});

/** The time in Italy, which is where the rain is. */
export const clock = (stamp: string) => CLOCK.format(when(stamp));
export const day = (date: string) => DAY.format(new Date(`${date}T12:00:00Z`));

export function ago(stamp: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - when(stamp).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min ago`;
}

export const mm = (value: number | null | undefined) =>
  value === null || value === undefined ? "–" : value.toFixed(1);

const WINDS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"];

export function going(moving: Moving): string | null {
  if (!moving) return null;
  if (moving.kmh < 2) return "almost still";
  return `${Math.round(moving.kmh)} km/h towards ${WINDS[Math.round(moving.towards_degrees / 45) % 8]}`;
}

export const place = (lon: number, lat: number) =>
  `${Math.abs(lat).toFixed(3)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(3)}° ${lon >= 0 ? "E" : "W"}`;
