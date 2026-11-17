"use client";

import { ALERT, clock, day, failed, going, mm, place } from "@/lib/format";
import type {
  AheadData,
  AreaRain,
  Failed,
  LayerKey,
  PointData,
  RadarRain,
  Selection,
  ZoneDay,
} from "@/lib/types";

type Props = {
  selection: Selection;
  layer: LayerKey;
  point: PointData | Failed | null;
  ahead: AheadData | Failed | null;
  radarRain: RadarRain | Failed | null;
  onClose: () => void;
};

const NOT_MEASURED = "No data here, or masked by the quality check. Not measured is not dry.";

function Swatch({ colour }: { colour: string | null | undefined }) {
  return <span className="swatch" style={{ background: colour ?? "transparent" }} aria-hidden />;
}

function Waiting({ lines = 2 }: { lines?: number }) {
  return (
    <div className="waiting" aria-label="Loading">
      {Array.from({ length: lines }, (_, k) => (
        <span key={k} />
      ))}
    </div>
  );
}

function Wrong({ what }: { what: Failed }) {
  return <p className="wrong">{what.error}</p>;
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section>
      <h3>
        {title}
        {hint && <small>{hint}</small>}
      </h3>
      {children}
    </section>
  );
}

function Risk({ zone }: { zone: ZoneDay }) {
  const risks = [
    ["hydrogeological", zone.hydrogeological],
    ["hydraulic", zone.hydraulic],
    ["thunderstorms", zone.thunderstorms],
  ].filter(([, level]) => level !== "none");
  return (
    <li>
      <span className="pill" style={{ background: ALERT[zone.colour] }} data-level={zone.colour}>
        {zone.level ? zone.colour : "no alert"}
      </span>
      <span>
        <b>{day(zone.day)}</b>
        {risks.length > 0 && <small>{risks.map(([name, level]) => `${name}: ${level}`).join(" · ")}</small>}
      </span>
    </li>
  );
}

function AreaTable({ rows }: { rows: AreaRain[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>last</th>
          <th>highest</th>
          <th>mean</th>
          <th title="The area with 10 mm or more">≥ 10 mm</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.hours}>
            <th>{row.hours} h</th>
            {row.error || row.max_mm === null ? (
              <td colSpan={3} className="muted" title={row.error ?? NOT_MEASURED}>
                not measured
              </td>
            ) : (
              <>
                <td>
                  <Swatch colour={row.colour} />
                  {mm(row.max_mm)} mm
                </td>
                <td>{mm(row.mean_mm)} mm</td>
                <td>{Math.round(row.km2_over?.["10"] ?? 0).toLocaleString("en-GB")} km²</td>
              </>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RainHere({ point, layer }: { point: PointData; layer: LayerKey }) {
  const { now } = point;
  return (
    <Section title="Rain here" hint={failed(now) ? undefined : `radar frame of ${clock(now.time)}`}>
      {failed(now) ? (
        <Wrong what={now} />
      ) : (
        <p className={`big${layer === "now" ? " active" : ""}`}>
          <Swatch colour={now.colour} />
          {now.measured ? (
            <>
              <b>{mm(now.mm_h)}</b> mm/h <small>right now</small>
            </>
          ) : (
            <span className="muted" title={NOT_MEASURED}>
              not measured right now
            </span>
          )}
        </p>
      )}
      <ol className="tiles">
        {point.rain.map((row) => (
          <li key={row.hours} className={String(row.hours) === layer ? "active" : undefined}>
            <small>last {row.hours} h</small>
            {row.error || !row.measured ? (
              <span className="muted" title={row.error ?? NOT_MEASURED}>
                n/m
              </span>
            ) : (
              <>
                <b>{mm(row.mm)}</b>
                <small>mm</small>
              </>
            )}
            <i style={{ background: row.colour ?? "transparent" }} />
          </li>
        ))}
      </ol>
      {point.rain
        .filter((row) => (row.rarity?.return_period_years ?? 0) >= 2)
        .map((row) => (
          <p key={row.hours} className="note">
            {mm(row.mm)} mm in {row.hours} h comes back here once in{" "}
            <b>
              {row.rarity!.at_least ? "50 years or more" : `about ${Math.round(row.rarity!.return_period_years!)} years`}
            </b>
            .
          </p>
        ))}
      {point.rain.some((row) => !row.error && !row.measured) && <p className="note">n/m: not measured, which is not dry.</p>}
    </Section>
  );
}

function Ahead({ ahead }: { ahead: AheadData | Failed | null }) {
  const moving = ahead && !failed(ahead) ? going(ahead.moving) : null;
  return (
    <Section title="Next two hours" hint="a forecast">
      {ahead === null ? (
        <Waiting />
      ) : failed(ahead) ? (
        <Wrong what={ahead} />
      ) : (
        <>
          <ol className="tiles three">
            {ahead.steps.map((step) => (
              <li key={step.minutes}>
                <small>next {step.minutes < 60 ? `${step.minutes} min` : `${step.minutes / 60} h`}</small>
                {step.forecast_mm === null ? (
                  <span className="muted" title="Not known, which is not dry.">
                    n/k
                  </span>
                ) : (
                  <>
                    <b>{mm(step.forecast_mm)}</b>
                    <small>mm</small>
                  </>
                )}
              </li>
            ))}
          </ol>
          <p className="note">
            {ahead.chance.probability !== null && (
              <>
                Chance of {ahead.chance.mm} mm or more in the next hour:{" "}
                <b>{Math.round(ahead.chance.probability * 100)} %</b>.{" "}
              </>
            )}
            {moving && <>The rain around is {moving === "almost still" ? moving : `moving at ${moving}`}. </>}
            The radar frame of {clock(ahead.issued)}, moved and not changed: storms born later are not in it.
          </p>
        </>
      )}
    </Section>
  );
}

function Zone({ zone }: { zone: PointData["zone"] }) {
  if (zone === null) {
    return (
      <Section title="Alert zone">
        <p className="note">This point is in no alert zone of the bulletin (the sea, or abroad).</p>
      </Section>
    );
  }
  if (failed(zone)) {
    return (
      <Section title="Alert zone">
        <Wrong what={zone} />
      </Section>
    );
  }
  return (
    <>
      <Section title="Official alert" hint={`bulletin of ${day(zone.issued.slice(0, 10))}`}>
        <ul className="alerts">
          {zone.days.map((one) => (
            <Risk key={one.day} zone={one} />
          ))}
        </ul>
      </Section>
      <Section title="Rain in the whole zone" hint={zone.name}>
        <AreaTable rows={zone.rain} />
      </Section>
    </>
  );
}

function Trust({ point }: { point: PointData }) {
  const { reliability: r, radars } = point;
  return (
    <Section title="How far to trust it">
      <p className="note">
        {r.score === null ? (
          "Outside what the radars see: no reliability here."
        ) : (
          <>
            The radar sees this place{" "}
            <b>{r.reliable ? "well" : "badly"}</b> (score {r.score.toFixed(2)} of 1)
            {r.nearest_radar && (
              <>
                : the nearest is {r.nearest_radar}, {Math.round(r.distance_km ?? 0)} km away
                {r.beam_height_m !== null && <>, and its beam passes {Math.round(r.beam_height_m).toLocaleString("en-GB")} m above the ground</>}
              </>
            )}
            .
          </>
        )}
      </p>
      {failed(radars) ? (
        <Wrong what={radars} />
      ) : (
        radars.radars.length > 0 && (
          <ul className="radars">
            {radars.radars.map((radar) => (
              <li key={radar.name} title={radar.on === null ? "status not known" : radar.on ? "on" : "off"}>
                <span className="dot" data-on={String(radar.on)} />
                {radar.name} <small>{Math.round(radar.distance_km)} km</small>
              </li>
            ))}
          </ul>
        )
      )}
    </Section>
  );
}

function Header({ selection, point }: { selection: Selection; point: PointData | Failed | null }) {
  const zone = point && !failed(point) && point.zone && !failed(point.zone) ? point.zone.name : null;
  const [kind, title] =
    selection.kind === "storm"
      ? ["Storm", `${selection.storm.peak_mm_h.toFixed(0)} mm/h at its peak`]
      : selection.kind === "radar"
        ? ["Radar", selection.radar.name]
        : ["Point", zone ?? (point ? "Outside the alert zones" : "…")];
  return (
    <header>
      <small>{kind}</small>
      <h2>{title}</h2>
      <p>
        {place(selection.lon, selection.lat)}
        {selection.kind !== "point" && zone && <> · {zone}</>}
      </p>
    </header>
  );
}

function Storm({ selection }: { selection: Extract<Selection, { kind: "storm" }> }) {
  const { storm } = selection;
  return (
    <Section title="This storm" hint="rain at 10 mm/h or more">
      <dl>
        <dt>Peak</dt>
        <dd>
          <Swatch colour={storm.colour} />
          {mm(storm.peak_mm_h)} mm/h
        </dd>
        <dt>Mean</dt>
        <dd>{mm(storm.mean_mm_h)} mm/h</dd>
        <dt>Area</dt>
        <dd>{Math.round(storm.area_km2).toLocaleString("en-GB")} km²</dd>
        <dt>Moving</dt>
        <dd>{going(storm.moving) ?? "not known"}</dd>
      </dl>
      <p className="note">An intensity at one instant, not the rain of an hour. Below: the rain at its peak.</p>
    </Section>
  );
}

function Radar({ selection, rain }: { selection: Extract<Selection, { kind: "radar" }>; rain: RadarRain | Failed | null }) {
  const { radar } = selection;
  const state = radar.on === null ? "not known" : radar.on ? "on" : "off";
  return (
    <>
      <Section title="This radar">
        <dl>
          <dt>Status</dt>
          <dd>
            <span className="dot" data-on={String(radar.on)} /> {state}
          </dd>
          {radar.band && (
            <>
              <dt>Band</dt>
              <dd>{radar.band}</dd>
            </>
          )}
          {radar.range_km && (
            <>
              <dt>Range</dt>
              <dd>{radar.range_km} km</dd>
            </>
          )}
          {radar.altitude_m !== null && (
            <>
              <dt>Altitude</dt>
              <dd>{Math.round(radar.altitude_m).toLocaleString("en-GB")} m</dd>
            </>
          )}
          {radar.operator && (
            <>
              <dt>Run by</dt>
              <dd>{radar.operator}</dd>
            </>
          )}
        </dl>
        <p className="note">
          {radar.in_mosaic
            ? "The status is what the DPC viewer shows: a file that is not documented open data."
            : "In the status file of the DPC viewer, but not among the radars of the mosaic."}
        </p>
      </Section>
      {radar.in_mosaic && (
        <Section title="Rain inside its range" hint={`${radar.range_km} km around`}>
          {rain === null ? <Waiting lines={4} /> : failed(rain) ? <Wrong what={rain} /> : <AreaTable rows={rain.rain} />}
        </Section>
      )}
    </>
  );
}

export default function Card({ selection, layer, point, ahead, radarRain, onClose }: Props) {
  const code =
    point && !failed(point)
      ? [point.python, ahead && !failed(ahead) ? ahead.python : null].filter(Boolean).join("\n")
      : null;
  return (
    <aside className="card" aria-live="polite">
      <button className="close" onClick={onClose} aria-label="Close">
        ×
      </button>
      <Header selection={selection} point={point} />
      <div className="scroll">
        {selection.kind === "storm" && <Storm selection={selection} />}
        {selection.kind === "radar" && <Radar selection={selection} rain={radarRain} />}
        {point === null ? (
          <section>
            <Waiting lines={5} />
          </section>
        ) : failed(point) ? (
          <section>
            <Wrong what={point} />
          </section>
        ) : (
          <>
            <RainHere point={point} layer={layer} />
            <Ahead ahead={ahead} />
            <Zone zone={point.zone} />
            <Trust point={point} />
          </>
        )}
        {code && (
          <details>
            <summary>The Python that gives this</summary>
            <pre>
              <code>{`from dpc_radar import RadarClient\nfrom dpc_radar.facts import Facts\n\n${code}`}</code>
            </pre>
          </details>
        )}
        <footer>Radar data, not an alert: the official alert is the DPC bulletin.</footer>
      </div>
    </aside>
  );
}
