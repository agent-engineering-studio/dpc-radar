"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import Card from "@/components/Card";
import type { Shown } from "@/components/MapView";
import { ago, ask, clock, failed } from "@/lib/format";
import type {
  AheadData,
  Collection,
  Failed,
  Film,
  Layer,
  LayerKey,
  PointData,
  RadarProps,
  RadarRain,
  Selection,
  StormProps,
  ZoneDay,
} from "@/lib/types";

// Leaflet needs a window: the map is drawn in the browser only.
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

const LAYERS: { key: LayerKey; label: string; long: string }[] = [
  { key: "now", label: "Now", long: "Rain rate now" },
  { key: "1", label: "1 h", long: "Rain of the last hour" },
  { key: "3", label: "3 h", long: "Rain of the last 3 hours" },
  { key: "6", label: "6 h", long: "Rain of the last 6 hours" },
  { key: "12", label: "12 h", long: "Rain of the last 12 hours" },
  { key: "24", label: "24 h", long: "Rain of the last 24 hours" },
];
const TOGGLES: { key: keyof Shown; label: string }[] = [
  { key: "rain", label: "Rain" },
  { key: "zones", label: "Alert zones" },
  { key: "storms", label: "Storms" },
  { key: "radars", label: "Radars" },
];
const REFRESH_MS = 120_000;
// The film: one frame every 600 ms, and a longer look at the newest before it starts again.
const FRAME_MS = 600;
const LAST_FRAME_MS = 1800;

const wrong = (error: unknown): Failed => ({
  error: error instanceof Error ? error.message : "it did not answer",
});

/** Ask the API again every two minutes: a new radar frame comes every five. */
function useLive<T>(path: string): T | Failed | null {
  const [found, setFound] = useState<{ path: string; value: T | Failed } | null>(null);
  useEffect(() => {
    const stop = new AbortController();
    const read = () =>
      ask<T>(path, stop.signal)
        .then((value) => setFound({ path, value }))
        .catch((error) => stop.signal.aborted || setFound({ path, value: wrong(error) }));
    read();
    const timer = setInterval(read, REFRESH_MS);
    return () => {
      stop.abort();
      clearInterval(timer);
    };
  }, [path]);
  return found?.path === path ? found.value : null;
}

/** Ask once for what was clicked; the answer to an older click is dropped. */
function useAnswer<T>(path: string | null): T | Failed | null {
  const [found, setFound] = useState<{ path: string; value: T | Failed } | null>(null);
  useEffect(() => {
    if (!path) return;
    const stop = new AbortController();
    ask<T>(path, stop.signal)
      .then((value) => setFound({ path, value }))
      .catch((error) => stop.signal.aborted || setFound({ path, value: wrong(error) }));
    return () => stop.abort();
  }, [path]);
  return found?.path === path ? found.value : null;
}

const good = <T,>(value: T | Failed | null): T | null => (value === null || failed(value) ? null : value);

export default function Demo() {
  const [key, setKey] = useState<LayerKey>("now");
  const [shown, setShown] = useState<Shown>({ rain: true, zones: true, storms: true, radars: false });
  const [selection, setSelection] = useState<Selection | null>(null);
  const [, tick] = useState(0);
  // The film of the layer: null is the live map, a number the frame shown.
  const [frame, setFrame] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const loaded = useRef(new Set<string>());
  const [arrived, setArrived] = useState(0);

  const layer = useLive<Layer>(`/api/layer/${key}`);
  const film = good(useAnswer<Film>(frame === null ? null : `/api/film/${key}`));
  const frames = film?.key === key ? film.frames : null;
  const zones = useLive<Collection<ZoneDay>>("/api/zones");
  const storms = useLive<Collection<StormProps>>("/api/storms");
  const radars = useLive<Collection<RadarProps>>("/api/radars");

  const at = selection && `lon=${selection.lon.toFixed(4)}&lat=${selection.lat.toFixed(4)}`;
  const point = useAnswer<PointData>(at && `/api/point?${at}`);
  const ahead = useAnswer<AheadData>(at && `/api/ahead?${at}`);
  const radarRain = useAnswer<RadarRain>(
    selection?.kind === "radar" && selection.radar.in_mosaic
      ? `/api/radar?name=${encodeURIComponent(selection.radar.name)}`
      : null,
  );

  // "5 min ago" grows old by itself.
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === "Escape" && setSelection(null);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

  // The pictures are asked for at once, and the film waits for the one it needs next.
  useEffect(() => {
    for (const one of frames ?? []) {
      if (loaded.current.has(one.image)) continue;
      const picture = new Image();
      picture.onload = () => {
        loaded.current.add(one.image);
        setArrived((n) => n + 1);
      };
      picture.src = one.image;
    }
  }, [frames]);
  useEffect(() => {
    if (!playing || !frames || frame === null) return;
    const last = frames.length - 1;
    const timer = setTimeout(
      () =>
        setFrame((now) => {
          if (now === null) return now;
          const next = now >= last ? 0 : now + 1;
          return loaded.current.has(frames[next].image) ? next : now;
        }),
      frame >= last ? LAST_FRAME_MS : FRAME_MS,
    );
    return () => clearTimeout(timer);
  }, [playing, frames, frame, arrived]);

  const play = () => {
    if (frame === null) setFrame(0);
    setPlaying(!playing);
  };
  const live = () => {
    setFrame(null);
    setPlaying(false);
  };
  const select = useCallback((next: Selection) => setSelection(next), []);
  const drawn = good(layer);
  const still = frames && frame !== null ? frames[Math.min(frame, frames.length - 1)] : null;
  const zone = point && !failed(point) && point.zone && !failed(point.zone) ? point.zone.name : null;
  const stormCount = good(storms)?.features.length;
  const off = good(radars)?.features.filter((f) => f.properties.on === false).length;
  const alerts = good(zones)?.features.filter((f) => f.properties.level > 0).length;
  const counts: Partial<Record<keyof Shown, string | undefined>> = {
    zones: alerts === undefined ? undefined : alerts ? `${alerts} on alert` : "no alert",
    storms: stormCount === undefined ? undefined : String(stormCount),
    radars: off === undefined ? undefined : off ? `${off} off` : undefined,
  };
  const problems = [layer, zones, storms, radars].filter(failed);

  return (
    <main className={selection ? "with-card" : undefined}>
      <MapView
        layer={drawn}
        image={still?.image ?? null}
        shown={shown}
        zones={good(zones)}
        storms={frame === null ? good(storms) : null}
        radars={good(radars)}
        selection={selection}
        zone={zone}
        onSelect={select}
      />

      <div className="panel controls">
        <h1>
          dpc-radar <span>the rain over Italy, live</span>
        </h1>
        <div className="segments" role="radiogroup" aria-label="What the map shows">
          {LAYERS.map((one) => (
            <button
              key={one.key}
              role="radio"
              aria-checked={key === one.key}
              title={one.long}
              onClick={() => {
                setKey(one.key);
                if (frame !== null) setFrame(0);
              }}
            >
              {one.label}
            </button>
          ))}
        </div>
        <div className="toggles">
          {TOGGLES.map((one) => (
            <label key={one.key} data-layer={one.key}>
              <input
                type="checkbox"
                checked={shown[one.key]}
                onChange={(e) => setShown({ ...shown, [one.key]: e.target.checked })}
              />
              <span>
                {one.label}
                {counts[one.key] && <small>{counts[one.key]}</small>}
              </span>
            </label>
          ))}
        </div>
        {!selection && <p className="hint">Click anywhere on the map: a zone, a storm, a radar or any point.</p>}
        {problems.map((problem, k) => (
          <p key={k} className="wrong">
            {problem.error}
          </p>
        ))}
      </div>

      <div className="panel legend">
        {drawn ? (
          <>
            <h2>
              {LAYERS.find((one) => one.key === drawn.key)?.long} <small>{drawn.unit}</small>
            </h2>
            <ol>
              {drawn.legend.map((step) => (
                <li key={step.from}>
                  <i style={{ background: step.colour }} />
                  {step.from}
                </li>
              ))}
            </ol>
            <p>
              <span className="veil" /> not measured, which is not dry
            </p>
            <p title={`${drawn.product} of ${drawn.time}, ${drawn.source}; check against the frame before: ${drawn.quality.verdict ?? "not made"}`}>
              Frame of <b>{clock(still?.time ?? drawn.time)}</b> ({ago(still?.time ?? drawn.time)}) · {drawn.source}
            </p>
            <div className="film">
              <button className="play" onClick={play} aria-label={playing ? "Pause" : "Play the last frames"}>
                {playing ? "❚❚" : "▶"}
              </button>
              {frame === null ? (
                <span>Play how the rain moved</span>
              ) : frames ? (
                <>
                  <input
                    type="range"
                    aria-label="Frame"
                    min={0}
                    max={frames.length - 1}
                    value={Math.min(frame, frames.length - 1)}
                    onChange={(e) => {
                      setPlaying(false);
                      setFrame(Number(e.target.value));
                    }}
                  />
                  <button className="live" onClick={live} title="Back to the newest frame, and the storms">
                    Live
                  </button>
                </>
              ) : (
                <span>Reading the frames…</span>
              )}
            </div>
          </>
        ) : (
          <p>{layer && failed(layer) ? "The rain is not available right now." : "Reading the newest radar frame…"}</p>
        )}
      </div>

      {selection && (
        <Card
          selection={selection}
          layer={key}
          point={point}
          ahead={ahead}
          radarRain={radarRain}
          onClose={() => setSelection(null)}
        />
      )}
    </main>
  );
}
