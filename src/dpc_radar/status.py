"""Which radars are on: the status file behind the DPC's own viewer.

The mosaic is made of some two dozen radars, and at any hour a few are off.
Where the only radar that covers a place is off, the mosaic has no data there
while it may be raining. The viewer of the DPC
(``radar.protezionecivile.gov.it``) draws the radars as green and red dots; it
reads them from a small GeoJSON file, one every five minutes, kept for about
two weeks. This module reads the same file::

    async with RadarClient() as radar:
        status = await radar.radar_status()
        [r.code for r in status.off]

Pure: nothing here does I/O. :meth:`dpc_radar.RadarClient.radar_status` sends
the request built by :func:`status_url` and hands the answer to
:func:`parse_status`.

What to know before trusting it:

* **The file is not part of the documented open data.** It has no licence and
  no promise: its address or its shape can change without notice. When it
  cannot be read the client answers :data:`dpc_radar.ABSENT`, and nothing else
  in the library depends on it.
* **The time is the one asked for.** The ``time`` inside the file is stale (in
  November 2026 it still said November 2025) and is ignored.
* **"On" is what the DPC reports, not what the mosaic holds.** On 17 November
  2026 Linate was reported off for two weeks while its whole circle had data,
  and Catania was reported on while its circle had none. Monte Lauro and Monte
  Pettinascura, reported off, were indeed missing. Read ``on`` as a hint to
  why a place has no data, never as proof that it has.
* The file lists three radars that are not in :data:`dpc_radar.SITES` (Reggio
  Calabria, Desio and Flero): no trace of them was found in the mosaic. Their
  ``site`` is ``None``. Salento is in ``SITES`` and not in the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from dpc_radar.errors import DpcRadarError
from dpc_radar.reliability import SITES, RadarSite

#: Where the viewer of the DPC keeps the status files.
STATUS_BASE: Final = "https://s3-prod-dpc-radar-webp-cache.s3.eu-south-1.amazonaws.com/SITES"
#: A new file every five minutes.
STATUS_PERIOD: Final = timedelta(minutes=5)
#: Said with every status, wherever it is shown.
STATUS_CAVEAT: Final = (
    "as reported by the DPC viewer, from a file that is not documented open data: "
    "a radar reported on may still be missing from the mosaic, and the other way round"
)

_BY_CODE: Final = {site.code: site for site in SITES if site.code is not None}


@dataclass(frozen=True)
class RadarStatus:
    """One radar as the status file has it."""

    #: The name the DPC gives it, such as ``"ILMONTE"`` or ``"XBAND1"``.
    code: str
    place: str
    province: str
    #: Who runs it: ``"DPC"``, ``"Regione"``, ``"Enav"``, ``"A.M"``.
    operator: str
    lon: float
    lat: float
    #: Metres above the sea, as the file says (rounded, like its coordinates).
    altitude: float | None
    on: bool
    #: The same radar in :data:`dpc_radar.SITES`; ``None`` if it is not there.
    site: RadarSite | None


@dataclass(frozen=True)
class NetworkStatus:
    """The radars of the network at a time, on and off."""

    #: The time the file is of (the one in its address), UTC.
    time: datetime
    radars: tuple[RadarStatus, ...]

    @property
    def off(self) -> tuple[RadarStatus, ...]:
        """The radars reported off."""
        return tuple(r for r in self.radars if not r.on)

    def of(self, site: RadarSite | str) -> bool | None:
        """Is a radar on? By its site, its name in ``SITES`` or its DPC code.

        ``None`` if the file does not list it: not known, which is not "off".
        """
        codes: list[str | None]
        if isinstance(site, RadarSite):
            codes = [site.code]
        else:
            codes = [site, *(s.code for s in SITES if s.name == site)]
        return next((r.on for r in self.radars if r.code in codes), None)

    def geojson(self) -> dict[str, Any]:
        """The status as a GeoJSON ``FeatureCollection`` of points (WGS84)."""
        return {
            "type": "FeatureCollection",
            "time": f"{self.time:%Y-%m-%dT%H:%MZ}",
            "source": "Dipartimento della Protezione Civile, radar viewer",
            "note": STATUS_CAVEAT,
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                    "properties": {
                        "code": r.code,
                        "name": None if r.site is None else r.site.name,
                        "place": r.place,
                        "province": r.province,
                        "operator": r.operator,
                        "altitude": r.altitude,
                        "on": r.on,
                        "in_mosaic": r.site is not None,
                    },
                }
                for r in self.radars
            ],
        }


def status_time(time: datetime) -> datetime:
    """The time of the file that covers ``time`` (aware): the five minutes it falls in."""
    if time.tzinfo is None:
        raise DpcRadarError("a time needs its zone: use datetime(..., tzinfo=UTC)")
    time = time.astimezone(UTC)
    return time.replace(minute=time.minute - time.minute % 5, second=0, microsecond=0)


def status_url(time: datetime, *, base: str = STATUS_BASE) -> str:
    """The address of the status file of ``time`` (aware)."""
    return f"{base.rstrip('/')}/{status_time(time):%Y/%m/%d/%H%M}/sites.geojson"


def parse_status(payload: dict[str, Any], time: datetime) -> NetworkStatus:
    """The status in a file; :class:`DpcRadarError` if it is not the file we know.

    The file is the head of a table and repeats the radars: the first row of
    each is kept (the repeated rows were never seen to disagree).
    """
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise DpcRadarError("the radar status file lists no radar")
    found: dict[str, RadarStatus] = {}
    try:
        for feature in features:
            about, shape = feature["properties"], feature["geometry"]
            code = str(about["nome"])
            if code in found:
                continue
            point = shape["coordinates"]
            lon, lat = point[0] if shape["type"] == "MultiPoint" else point
            height = about.get("quota")
            found[code] = RadarStatus(
                code=code,
                place=str(about.get("localita") or ""),
                province=str(about.get("provincia") or ""),
                operator=str(about.get("gestore") or ""),
                lon=float(lon),
                lat=float(lat),
                altitude=None if height is None else float(height),
                on=bool(about["status"]),
                site=_BY_CODE.get(code),
            )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DpcRadarError(f"the radar status file is not as expected ({exc!r})") from exc
    return NetworkStatus(status_time(time), tuple(found.values()))


__all__ = [
    "STATUS_BASE",
    "STATUS_CAVEAT",
    "STATUS_PERIOD",
    "NetworkStatus",
    "RadarStatus",
    "parse_status",
    "status_time",
    "status_url",
]
