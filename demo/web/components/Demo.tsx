"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import Card from "@/components/Card";
import type { Shown } from "@/components/MapView";
import { ask, clock, failed } from "@/lib/format";
import { LANGS, LangContext, TEXT, type Lang } from "@/lib/i18n";
import type { Start, Tiles } from "@/lib/start";
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

const LAYERS: LayerKey[] = ["now", "1", "3", "6", "12", "24"];
const TOGGLES: (keyof Shown)[] = ["rain", "zones", "storms", "radars"];
const REFRESH_MS = 120_000;
// The film: one frame every 600 ms, and a longer look at the newest before it starts again.
const FRAME_MS = 600;
const LAST_FRAME_MS = 1800;

const wrong = (error: unknown): Failed => ({
  error: error instanceof Error ? error.message : "—",
});

/** Italian for who reads Italian, English for everyone else; a choice made is kept. */
function useLang(): [Lang, (lang: Lang) => void] {
  const [lang, setLang] = useState<Lang>("en");
  useEffect(() => {
    let kept: string | null = null;
    try {
      kept = localStorage.getItem("lang");
    } catch {}
    const first = LANGS.find((one) => one === kept) ?? (navigator.language.startsWith("it") ? "it" : "en");
    setLang(first);
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const choose = (next: Lang) => {
    setLang(next);
    try {
      localStorage.setItem("lang", next);
    } catch {}
  };
  return [lang, choose];
}

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

const DOCS = "https://agentengineering.it/dpc-radar/";
const CODE = "https://github.com/agent-engineering-studio/dpc-radar";
const ALERTS = "https://rischi.protezionecivile.gov.it/it/meteo-idro/allertamento/";

export default function Demo({ start, tiles }: { start: Start; tiles: Tiles }) {
  const [lang, setLang] = useLang();
  const t = TEXT[lang];
  const [key, setKey] = useState<LayerKey>(start.layer);
  const [shown, setShown] = useState<Shown>({ rain: true, zones: true, storms: true, radars: false });
  // A page about a place opens with the numbers of that place.
  const place: Selection | null = start.given ? { kind: "point", lon: start.lon, lat: start.lat } : null;
  const [selection, setSelection] = useState<Selection | null>(place);
  const [home, setHome] = useState(0);
  const [, tick] = useState(0);
  // The film of the layer: null is the live map, a number the frame shown.
  const [frame, setFrame] = useState<number | null>(start.frame);
  const [playing, setPlaying] = useState(false);
  const loaded = useRef(new Set<string>());
  const [arrived, setArrived] = useState(0);

  const layer = useLive<Layer>(`/api/layer/${key}`);
  const film = good(useLive<Film>(`/api/film/${key}`));
  const frames = film?.key === key ? film.frames : null;
  const filming = frame !== null;
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
    if (!filming) return;
    for (const one of frames ?? []) {
      if (loaded.current.has(one.image)) continue;
      const picture = new Image();
      picture.onload = () => {
        loaded.current.add(one.image);
        setArrived((n) => n + 1);
      };
      picture.src = one.image;
    }
  }, [frames, filming]);
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
    zones: alerts === undefined ? undefined : alerts ? t.onAlert(alerts) : t.noAlert,
    storms: stormCount === undefined ? undefined : String(stormCount),
    radars: off === undefined ? undefined : off ? t.radarsOff(off) : undefined,
  };
  const problems = [layer, zones, storms, radars].filter(failed);

  const atPlace =
    place !== null && selection?.kind === "point" && selection.lon === place.lon && selection.lat === place.lat;
  const goHome = () => {
    setSelection(place);
    setHome((n) => n + 1);
  };

  return (
    <LangContext.Provider value={t}>
      <title>{start.place ? `${start.place} · ${t.title}` : t.title}</title>
      <a className="visually-hidden-focusable skip" href="#content">
        {t.skip}
      </a>
      <header className="it-header-wrapper">
        <div className="it-header-slim-wrapper">
          <div className="container-xxl">
            <div className="it-header-slim-wrapper-content">
              <span className="navbar-brand">{t.owner}</span>
              <div className="it-header-slim-right-zone">
                <div className="langs" role="radiogroup" aria-label={t.language}>
                  {LANGS.map((one) => (
                    <button key={one} role="radio" aria-checked={lang === one} onClick={() => setLang(one)}>
                      {one.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="it-nav-wrapper">
          <div className="it-header-center-wrapper">
            <div className="container-xxl">
              <div className="it-header-center-content-wrapper">
                <div className="it-brand-wrapper">
                  <div className="it-brand-text">
                    <h1 className="it-brand-title">{start.place ?? "dpc-radar"}</h1>
                    <div className="it-brand-tagline">{start.place ? t.taglineHere : t.tagline}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </header>

      <main id="content" className="container-xxl">
        <div className="row g-3">
          <div className="col-lg-8">
            <div className="bar">
              <div className="btn-group" role="radiogroup" aria-label={t.layersLabel}>
                {LAYERS.map((one) => (
                  <button
                    key={one}
                    type="button"
                    role="radio"
                    aria-checked={key === one}
                    className={`btn btn-xs ${key === one ? "btn-primary" : "btn-outline-primary"}`}
                    title={t.layers[one][1]}
                    onClick={() => {
                      setKey(one);
                      if (frame !== null) setFrame(0);
                    }}
                  >
                    {t.layers[one][0]}
                  </button>
                ))}
              </div>
              <div className="shown">
                {TOGGLES.map((one) => (
                  <div key={one} className="form-check form-check-inline" data-layer={one}>
                    <input
                      id={`show-${one}`}
                      type="checkbox"
                      checked={shown[one]}
                      onChange={(e) => setShown({ ...shown, [one]: e.target.checked })}
                    />
                    <label htmlFor={`show-${one}`}>
                      {t.toggles[one]}
                      {counts[one] && <small>{counts[one]}</small>}
                    </label>
                  </div>
                ))}
              </div>
              <button type="button" className="btn btn-outline-primary btn-xs home" onClick={goHome}>
                {t.backTo(start.place ?? t.start)}
              </button>
            </div>
            {problems.map((problem, k) => (
              <div key={k} className="alert alert-danger" role="alert">
                {problem.error === "—" ? t.noAnswer : problem.error}
              </div>
            ))}

            <div className="stage">
              <MapView
                start={start}
                tiles={tiles}
                home={home}
                layer={drawn}
                image={still?.image ?? null}
                shown={shown}
                text={t}
                zones={good(zones)}
                storms={frame === null ? good(storms) : null}
                radars={good(radars)}
                selection={selection}
                zone={zone}
                onSelect={select}
              />
              <div className="panel legend">
                {drawn ? (
                  <>
                    <h2>
                      {t.layers[drawn.key][1]} <small>{drawn.unit}</small>
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
                      <span className="veil" /> {t.notMeasuredDry}
                    </p>
                    <p title={t.frameTitle(drawn.product, drawn.time, t.source(drawn.source), drawn.quality.verdict)}>
                      {t.frameOf} <b>{clock(still?.time ?? drawn.time)}</b> ({t.ago(still?.time ?? drawn.time)}) ·{" "}
                      {t.source(drawn.source)}
                    </p>
                    <div className="film">
                      <button
                        type="button"
                        className="btn btn-primary btn-xs play"
                        onClick={play}
                        title={t.playTitle}
                        aria-pressed={playing}
                      >
                        <i aria-hidden>{playing ? "❚❚" : "▶"}</i> {playing ? t.pause : t.play}
                      </button>
                      {frames ? (
                        <input
                          type="range"
                          aria-label={t.frame}
                          min={0}
                          max={frames.length - 1}
                          value={frame === null ? frames.length - 1 : Math.min(frame, frames.length - 1)}
                          onChange={(e) => {
                            setPlaying(false);
                            setFrame(Number(e.target.value));
                          }}
                        />
                      ) : (
                        <span>{t.readingFrames}</span>
                      )}
                      <button
                        type="button"
                        className="btn btn-outline-primary btn-xs live"
                        onClick={live}
                        title={t.liveTitle}
                        aria-pressed={frame === null}
                      >
                        {t.live}
                      </button>
                    </div>
                  </>
                ) : (
                  <p>{layer && failed(layer) ? t.rainUnavailable : t.readingNewest}</p>
                )}
              </div>
            </div>
          </div>

          <div className="col-lg-4">
            {selection ? (
              <Card
                selection={selection}
                layer={key}
                point={point}
                ahead={ahead}
                radarRain={radarRain}
                storms={frame === null ? good(storms) : null}
                name={atPlace ? start.place : null}
                onClose={() => setSelection(null)}
              />
            ) : (
              <div className="callout note">
                <div className="callout-title">{t.readTitle}</div>
                <p>{t.hint}</p>
              </div>
            )}
          </div>
        </div>
      </main>

      <footer className="it-footer">
        <div className="it-footer-main">
          <div className="container-xxl">
            <div className="row py-4">
              <section className="col-md-4">
                <h2 className="h6">{t.dataTitle}</h2>
                <p>{t.dataText}</p>
              </section>
              <section className="col-md-4">
                <h2 className="h6">{t.alertTitle}</h2>
                <p>
                  {t.alertText} <a href={ALERTS}>{t.alertLink}</a>
                </p>
              </section>
              <section className="col-md-4">
                <h2 className="h6">{t.howTitle}</h2>
                <p>{t.howText}</p>
              </section>
            </div>
          </div>
        </div>
        <div className="it-footer-small-prints clearfix">
          <div className="container-xxl">
            <ul className="it-footer-small-prints-list list-inline mb-0 d-flex flex-column flex-md-row">
              <li className="list-inline-item">
                <a href={DOCS}>dpc-radar · {t.docs}</a>
              </li>
              <li className="list-inline-item">
                <a href={CODE}>{t.code}</a>
              </li>
            </ul>
          </div>
        </div>
      </footer>
    </LangContext.Provider>
  );
}
