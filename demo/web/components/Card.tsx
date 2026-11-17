"use client";

import { ALERT, clock, failed, place } from "@/lib/format";
import { useText } from "@/lib/i18n";
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

function Swatch({ colour }: { colour: string | null | undefined }) {
  return <span className="swatch" style={{ background: colour ?? "transparent" }} aria-hidden />;
}

function Waiting({ lines = 2 }: { lines?: number }) {
  const t = useText();
  return (
    <div className="waiting" aria-label={t.loading}>
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
  const t = useText();
  const risks = (["hydrogeological", "hydraulic", "thunderstorms"] as const)
    .map((name) => [t.risks[name], zone[name]] as const)
    .filter(([, level]) => level !== "none");
  return (
    <li>
      <span className="pill" style={{ background: ALERT[zone.colour] }} data-level={zone.colour}>
        {t.colours[zone.colour]}
      </span>
      <span>
        <b>{t.day(zone.day)}</b>
        {risks.length > 0 && <small>{risks.map(([name, level]) => `${name}: ${t.colours[level as ZoneDay["colour"]] ?? level}`).join(" · ")}</small>}
      </span>
    </li>
  );
}

function AreaTable({ rows }: { rows: AreaRain[] }) {
  const t = useText();
  return (
    <table>
      <thead>
        <tr>
          <th>{t.last}</th>
          <th>{t.highest}</th>
          <th>{t.mean}</th>
          <th title={t.areaOver}>≥ 10 mm</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.hours}>
            <th>{row.hours} h</th>
            {row.error || row.max_mm === null ? (
              <td colSpan={3} className="muted" title={row.error ?? t.notMeasured}>
                {t.notMeasuredShort}
              </td>
            ) : (
              <>
                <td>
                  <Swatch colour={row.colour} />
                  {t.mm(row.max_mm)} mm
                </td>
                <td>{t.mm(row.mean_mm)} mm</td>
                <td>{t.whole(row.km2_over?.["10"] ?? 0)} km²</td>
              </>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RainHere({ point, layer }: { point: PointData; layer: LayerKey }) {
  const t = useText();
  const { now } = point;
  return (
    <Section title={t.rainHere} hint={failed(now) ? undefined : t.radarFrameOf(clock(now.time))}>
      {failed(now) ? (
        <Wrong what={now} />
      ) : (
        <p className={`big${layer === "now" ? " active" : ""}`}>
          <Swatch colour={now.colour} />
          {now.measured ? (
            <>
              <b>{t.mm(now.mm_h)}</b> mm/h <small>{t.rightNow}</small>
            </>
          ) : (
            <span className="muted" title={t.notMeasured}>
              {t.notMeasuredNow}
            </span>
          )}
        </p>
      )}
      <ol className="tiles">
        {point.rain.map((row) => (
          <li key={row.hours} className={String(row.hours) === layer ? "active" : undefined}>
            <small>{t.lastHours(row.hours)}</small>
            {row.error || !row.measured ? (
              <span className="muted" title={row.error ?? t.notMeasured}>
                {t.nm}
              </span>
            ) : (
              <>
                <b>{t.mm(row.mm)}</b>
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
            {t.comesBack(t.mm(row.mm), row.hours)}
            <b>
              {row.rarity!.at_least ? t.fiftyYears : t.aboutYears(Math.round(row.rarity!.return_period_years!))}
            </b>
            .
          </p>
        ))}
      {point.rain.some((row) => !row.error && !row.measured) && <p className="note">{t.nmNote}</p>}
    </Section>
  );
}

function Ahead({ ahead }: { ahead: AheadData | Failed | null }) {
  const t = useText();
  const moving = ahead && !failed(ahead) ? t.going(ahead.moving) : null;
  return (
    <Section title={t.nextTwoHours} hint={t.aForecast}>
      {ahead === null ? (
        <Waiting />
      ) : failed(ahead) ? (
        <Wrong what={ahead} />
      ) : (
        <>
          <ol className="tiles three">
            {ahead.steps.map((step) => (
              <li key={step.minutes}>
                <small>{t.next} {step.minutes < 60 ? `${step.minutes} min` : `${step.minutes / 60} h`}</small>
                {step.forecast_mm === null ? (
                  <span className="muted" title={t.notKnownDry}>
                    {t.nk}
                  </span>
                ) : (
                  <>
                    <b>{t.mm(step.forecast_mm)}</b>
                    <small>mm</small>
                  </>
                )}
              </li>
            ))}
          </ol>
          <p className="note">
            {ahead.chance.probability !== null && (
              <>
                {t.chanceOf(ahead.chance.mm)}
                <b>{Math.round(ahead.chance.probability * 100)} %</b>.{" "}
              </>
            )}
            {moving && t.rainAround(moving, moving === t.almostStill)}
            {t.movedNotChanged(clock(ahead.issued))}
          </p>
        </>
      )}
    </Section>
  );
}

function Zone({ zone }: { zone: PointData["zone"] }) {
  const t = useText();
  if (zone === null) {
    return (
      <Section title={t.alertZone}>
        <p className="note">{t.noZone}</p>
      </Section>
    );
  }
  if (failed(zone)) {
    return (
      <Section title={t.alertZone}>
        <Wrong what={zone} />
      </Section>
    );
  }
  return (
    <>
      <Section title={t.officialAlert} hint={t.bulletinOf(t.day(zone.issued.slice(0, 10)))}>
        <ul className="alerts">
          {zone.days.map((one) => (
            <Risk key={one.day} zone={one} />
          ))}
        </ul>
      </Section>
      <Section title={t.rainInZone} hint={zone.name}>
        <AreaTable rows={zone.rain} />
      </Section>
    </>
  );
}

function Trust({ point }: { point: PointData }) {
  const t = useText();
  const { reliability: r, radars } = point;
  return (
    <Section title={t.trust}>
      <p className="note">
        {r.score === null ? (
          t.outsideRadars
        ) : (
          <>
            {t.radarSees}
            <b>{r.reliable ? t.well : t.badly}</b>
            {t.score(r.score.toLocaleString(t.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))}
            {r.nearest_radar && (
              <>
                {t.nearest(r.nearest_radar, Math.round(r.distance_km ?? 0))}
                {r.beam_height_m !== null && t.beam(t.whole(r.beam_height_m))}
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
              <li key={radar.name} title={t.state(radar.on)}>
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
  const t = useText();
  const zone = point && !failed(point) && point.zone && !failed(point.zone) ? point.zone.name : null;
  const [kind, title] =
    selection.kind === "storm"
      ? [t.storm, t.atPeak(selection.storm.peak_mm_h.toFixed(0))]
      : selection.kind === "radar"
        ? [t.radar, selection.radar.name]
        : [t.point, zone ?? (point ? t.outsideZones : "…")];
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
  const t = useText();
  const { storm } = selection;
  return (
    <Section title={t.thisStorm} hint={t.stormHint}>
      <dl>
        <dt>{t.peak}</dt>
        <dd>
          <Swatch colour={storm.colour} />
          {t.mm(storm.peak_mm_h)} mm/h
        </dd>
        <dt>{t.meanLabel}</dt>
        <dd>{t.mm(storm.mean_mm_h)} mm/h</dd>
        <dt>{t.area}</dt>
        <dd>{t.whole(storm.area_km2)} km²</dd>
        <dt>{t.moving}</dt>
        <dd>{t.going(storm.moving) ?? t.notKnown}</dd>
      </dl>
      <p className="note">{t.stormNote}</p>
    </Section>
  );
}

function Radar({ selection, rain }: { selection: Extract<Selection, { kind: "radar" }>; rain: RadarRain | Failed | null }) {
  const t = useText();
  const { radar } = selection;
  const state = radar.on === null ? t.notKnown : t.state(radar.on);
  return (
    <>
      <Section title={t.thisRadar}>
        <dl>
          <dt>{t.statusLabel}</dt>
          <dd>
            <span className="dot" data-on={String(radar.on)} /> {state}
          </dd>
          {radar.band && (
            <>
              <dt>{t.band}</dt>
              <dd>{radar.band}</dd>
            </>
          )}
          {radar.range_km && (
            <>
              <dt>{t.range}</dt>
              <dd>{radar.range_km} km</dd>
            </>
          )}
          {radar.altitude_m !== null && (
            <>
              <dt>{t.altitude}</dt>
              <dd>{t.whole(radar.altitude_m)} m</dd>
            </>
          )}
          {radar.operator && (
            <>
              <dt>{t.runBy}</dt>
              <dd>{radar.operator}</dd>
            </>
          )}
        </dl>
        <p className="note">
          {radar.in_mosaic
            ? t.statusNote
            : t.notInMosaic}
        </p>
      </Section>
      {radar.in_mosaic && (
        <Section title={t.rainInRange} hint={t.around(radar.range_km)}>
          {rain === null ? <Waiting lines={4} /> : failed(rain) ? <Wrong what={rain} /> : <AreaTable rows={rain.rain} />}
        </Section>
      )}
    </>
  );
}

export default function Card({ selection, layer, point, ahead, radarRain, onClose }: Props) {
  const t = useText();
  const code =
    point && !failed(point)
      ? [point.python, ahead && !failed(ahead) ? ahead.python : null].filter(Boolean).join("\n")
      : null;
  return (
    <aside className="card" aria-live="polite">
      <button className="close" onClick={onClose} aria-label={t.close}>
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
            <summary>{t.python}</summary>
            <pre>
              <code>{`from dpc_radar import RadarClient\nfrom dpc_radar.facts import Facts\n\n${code}`}</code>
            </pre>
          </details>
        )}
        <footer>{t.footer}</footer>
      </div>
    </aside>
  );
}
