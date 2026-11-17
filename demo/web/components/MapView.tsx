"use client";

import L from "leaflet";
import { useEffect, useRef } from "react";
import { ALERT } from "@/lib/format";
import type { Text } from "@/lib/i18n";
import type { Collection, Layer, RadarProps, Selection, StormProps, ZoneDay } from "@/lib/types";

// Rome, until the size of the window is known; then Italy as large as the window holds it,
// from the Alps to Sicily: its middle is 40 km from Rome.
const ROME: L.LatLngExpression = [41.9, 12.5];
const ZOOM = 6;
const ITALY: L.LatLngBoundsExpression = [
  [36.6, 6.6],
  [47.1, 18.55],
];

const ON = "#1a9850";
const OFF = "#d7191c";
const UNKNOWN = "#7a8290";

export type Shown = { rain: boolean; zones: boolean; storms: boolean; radars: boolean };

type Props = {
  layer: Layer | null;
  /** The picture of an older frame, while the film plays; otherwise the one of the layer. */
  image: string | null;
  shown: Shown;
  /** The words of the tips, in the language of the page. */
  text: Text;
  zones: Collection<ZoneDay> | null;
  storms: Collection<StormProps> | null;
  radars: Collection<RadarProps> | null;
  selection: Selection | null;
  zone: string | null;
  onSelect: (selection: Selection) => void;
};

const zoneStyle = (zone: ZoneDay, selected: boolean): L.PathOptions => ({
  color: selected ? "#111827" : ALERT[zone.colour],
  weight: selected ? 2.5 : zone.level ? 1.2 : 0.6,
  opacity: selected ? 1 : zone.level ? 0.9 : 0.45,
  fillColor: ALERT[zone.colour],
  fillOpacity: zone.level ? (selected ? 0.4 : 0.28) : selected ? 0.08 : 0,
});

const radarColour = (on: boolean | null) => (on === null ? UNKNOWN : on ? ON : OFF);

export default function MapView(props: Props) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const rain = useRef<L.ImageOverlay | null>(null);
  const zones = useRef<L.GeoJSON | null>(null);
  const onSelect = useRef(props.onSelect);
  useEffect(() => {
    onSelect.current = props.onSelect;
  });

  useEffect(() => {
    const m = L.map(box.current!, {
      center: ROME,
      zoom: ZOOM,
      minZoom: 5,
      maxZoom: 13,
      zoomSnap: 0.25,
      zoomControl: false,
      maxBounds: [
        [30, -4],
        [52, 29],
      ],
    });
    m.fitBounds(ITALY, { animate: false });
    L.control.zoom({ position: "bottomright" }).addTo(m);
    L.control.scale({ imperial: false, position: "bottomright" }).addTo(m);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · ' +
        '<a href="https://www.protezionecivile.gov.it/">Dipartimento della Protezione Civile</a>, CC BY-SA 4.0',
    }).addTo(m);
    // One pane a layer, so that the rain stays under the shapes one clicks on.
    for (const [name, z] of [
      ["rain", 350],
      ["zones", 410],
      ["ranges", 420],
      ["storms", 430],
      ["radars", 440],
      ["pin", 450],
    ] as const) {
      m.createPane(name).style.zIndex = String(z);
    }
    m.getPane("ranges")!.style.pointerEvents = "none";
    m.getPane("pin")!.style.pointerEvents = "none";
    m.on("click", (e) =>
      onSelect.current({ kind: "point", lon: e.latlng.lng, lat: e.latlng.lat }),
    );
    map.current = m;
    return () => {
      m.remove();
      map.current = rain.current = zones.current = null;
    };
  }, []);

  // The rain: one picture over all of Italy.
  const { layer, shown, text: t } = props;
  const image = props.image ?? layer?.image;
  const bounds = layer?.bounds;
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!image || !bounds || !shown.rain) {
      rain.current?.remove();
      rain.current = null;
      return;
    }
    if (rain.current) rain.current.setUrl(image);
    else {
      rain.current = L.imageOverlay(image, bounds, {
        pane: "rain",
        opacity: 0.85,
        interactive: false,
      }).addTo(m);
    }
  }, [image, bounds, shown.rain]);

  // The alert zones. A click on one falls through to the map: it is a click on a point of it.
  useEffect(() => {
    const m = map.current;
    if (!m || !props.zones || !shown.zones) return;
    const drawn = L.geoJSON(props.zones as GeoJSON.GeoJsonObject, {
      pane: "zones",
      style: (feature) => zoneStyle(feature!.properties as ZoneDay, false),
      onEachFeature: (feature, shape) => {
        const zone = feature.properties as ZoneDay;
        shape.bindTooltip(`<b>${zone.zone}</b><br>${t.alert(zone)}`, { sticky: true, className: "tip" });
      },
    }).addTo(m);
    zones.current = drawn;
    return () => {
      drawn.remove();
      zones.current = null;
    };
  }, [props.zones, shown.zones, t]);

  useEffect(() => {
    zones.current?.setStyle((feature) => {
      const zone = feature!.properties as ZoneDay;
      return zoneStyle(zone, zone.zone === props.zone);
    });
  }, [props.zone, props.zones, shown.zones, t]);

  // The storms: the patch at 10 mm/h or more, and a dot on its peak that is easy to hit.
  useEffect(() => {
    const m = map.current;
    if (!m || !props.storms || !shown.storms) return;
    const group = L.layerGroup().addTo(m);
    for (const feature of props.storms.features) {
      const storm = feature.properties;
      const select = (e: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(e);
        onSelect.current({ kind: "storm", lon: storm.peak_lon, lat: storm.peak_lat, storm });
      };
      const moving = t.going(storm.moving);
      const tip = `<b>${t.storm} · ${storm.peak_mm_h.toFixed(0)} mm/h</b><br>${t.whole(storm.area_km2)} km²${moving ? ` · ${moving}` : ""}`;
      L.geoJSON(feature as GeoJSON.Feature, {
        pane: "storms",
        style: { color: "#b3124a", weight: 1.5, fillColor: "#e3245e", fillOpacity: 0.3 },
      })
        .bindTooltip(tip, { sticky: true, className: "tip" })
        .on("click", select)
        .addTo(group);
      L.circleMarker([storm.peak_lat, storm.peak_lon], {
        pane: "storms",
        radius: 7,
        color: "#ffffff",
        weight: 2,
        fillColor: "#e3245e",
        fillOpacity: 1,
      })
        .bindTooltip(tip, { className: "tip", direction: "top" })
        .on("click", select)
        .addTo(group);
    }
    return () => {
      group.remove();
    };
  }, [props.storms, shown.storms, t]);

  // The radars: green is on, red is off, grey is not known.
  useEffect(() => {
    const m = map.current;
    if (!m || !props.radars || !shown.radars) return;
    const group = L.layerGroup().addTo(m);
    for (const feature of props.radars.features) {
      const [lon, lat] = (feature.geometry as GeoJSON.Point).coordinates;
      const radar = { ...feature.properties, lon, lat };
      L.circleMarker([lat, lon], {
        pane: "radars",
        radius: radar.in_mosaic ? 6 : 4,
        color: radarColour(radar.on),
        weight: 3,
        fillColor: "#ffffff",
        fillOpacity: 1,
      })
        .bindTooltip(`<b>${radar.name}</b><br>${t.radar.toLowerCase()} · ${t.state(radar.on)}`, { className: "tip", direction: "top" })
        .on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          onSelect.current({ kind: "radar", lon, lat, radar });
        })
        .addTo(group);
    }
    return () => {
      group.remove();
    };
  }, [props.radars, shown.radars, t]);

  // What was clicked: a pin, and around a radar the circle it sees.
  const { selection } = props;
  useEffect(() => {
    const m = map.current;
    if (!m || !selection) return;
    const group = L.layerGroup().addTo(m);
    const at: L.LatLngExpression = [selection.lat, selection.lon];
    if (selection.kind === "radar" && selection.radar.range_km) {
      L.circle(at, {
        pane: "ranges",
        radius: selection.radar.range_km * 1000,
        color: radarColour(selection.radar.on),
        weight: 1.5,
        dashArray: "6 6",
        fillOpacity: 0.05,
      }).addTo(group);
    }
    L.circleMarker(at, { pane: "pin", radius: 11, color: "#111827", weight: 2, fill: false }).addTo(group);
    L.circleMarker(at, {
      pane: "pin",
      radius: 3,
      color: "#111827",
      weight: 0,
      fillColor: "#111827",
      fillOpacity: 1,
    }).addTo(group);
    return () => {
      group.remove();
    };
  }, [selection]);

  return <div ref={box} className="map" aria-label={t.mapLabel} />;
}
