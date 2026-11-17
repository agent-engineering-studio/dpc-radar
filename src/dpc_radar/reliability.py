"""Where the radar can be trusted: a static map of Italy, one value per km.

A radar that reads zero is not always a dry sky. Far from the sites the beam
passes kilometres above the ground, behind a mountain it is cut, and some
radars are off for weeks: there the mosaic shows little or nothing while it
rains. On 10 October 2026 it missed a cloudburst on Lipari this way.

:func:`reliability` answers "how much can I trust the radar here?" with a score
between 0 and 1 and the parts it is made of:

* ``visibility``: how low the radar sees. From the sites (:data:`SITES`) and a
  terrain model, the height above the ground of the lowest beam that reaches
  the point without being cut by a mountain (``beam_height``, metres): 1 up to
  1500 m, 0 from 5000 m, where the beam passes above most of the rain;
* ``coverage``: the share of the hourly frames in which the mosaic has data
  at the point (the radars that cover it were on). Where the mosaic never has
  data, as in the west of Sicily, it is 0 and so is the score;
* ``agreement``: how close the radar alone (SRT1) is to the «Merging» of radar
  and rain gauges (CUM3) over the same months, as the smaller of the two
  ratios. ``None`` where too little rain fell to tell, and over the open sea,
  where there are no gauges to compare with.

The score is the lowest of the parts that are known: a point is as good as its
weakest side. ``visibility`` is drawn with every radar on; the months a radar
was off are in ``coverage``. Below :data:`RELIABLE_FROM` a radar zero must not be read as "dry".

The map ships with the package as a small GeoTIFF on the 1 km grid of the
radar mosaic; ``tools/build_reliability.py`` builds it again. It is an estimate
made from outside: the DPC does not publish a quality map, nor the position of
the gauges that enter the «Merging» (so the density of gauges, which would be
a fourth part, is not here). ``coverage`` and ``agreement`` describe the months
the map was built on (see ``ReliabilityMap.built_on``), not for ever.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cache
from importlib.resources import as_file, files
from typing import Final, Literal

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject
from rasterio.warp import transform as warp_points

from dpc_radar.grid import RainGrid

#: From this score up the radar can be trusted at a point.
RELIABLE_FROM: Final = 0.5
#: The file of the map inside the package.
MAP_FILE: Final = "reliability.tif"
#: The bands of the file, in order. Bytes: 0-100 are hundredths (``beam_height``:
#: units of 50 m), 255 is "not known".
BANDS: Final = ("score", "visibility", "coverage", "agreement", "beam_height")
Part = Literal["score", "visibility", "coverage", "agreement", "beam_height"]
_NODATA = 255
_BEAM_STEP_M = 50.0


@dataclass(frozen=True)
class RadarSite:
    """One radar of the national network.

    ``source`` says where the position comes from: ``"dpc"`` the Department's
    own table; ``"mosaic"`` the range circle fitted on the no-data mask of the
    mosaic (within about 1 km); ``"literature"`` a published table, for radars
    whose circle is always hidden under the others. ``range_km`` is the radius
    of the circle in the mosaic (``"literature"`` sites: assumed). ``code`` is
    the name the DPC gives the radar in its status file
    (:mod:`dpc_radar.status`); ``None`` if the file does not list it.
    """

    name: str
    lon: float
    lat: float
    altitude: float
    band: Literal["C", "X"]
    range_km: float
    source: Literal["dpc", "mosaic", "literature"]
    code: str | None = None


#: The radars that feed the mosaic. The seven of the DPC come from its table
#: (Rete Radar Nazionale, technical annex); the others are named there without
#: coordinates and were placed by fitting their range circles on the mosaic.
#: The status file of the DPC viewer (:mod:`dpc_radar.status`) has all of them
#: but Salento, each within 1.4 km of the position here; its own coordinates
#: are rounded, so the ones here were kept.
SITES: Final[tuple[RadarSite, ...]] = (
    RadarSite("Il Monte", 14.624, 41.939, 692, "C", 200, "dpc", "ILMONTE"),
    RadarSite("Monte Crocione", 10.607, 43.956, 1017, "C", 200, "dpc", "CROCIONE"),
    RadarSite("Monte Serano", 12.791, 42.856, 1428, "C", 200, "dpc", "SERANO"),
    RadarSite("Monte Pettinascura", 16.624, 39.373, 1708, "C", 200, "dpc", "PETTINASCURA"),
    RadarSite("Zouf Plan", 12.974, 46.556, 1999, "C", 200, "dpc", "ZOUFPLAN"),
    RadarSite("Monte Lauro", 14.824, 37.123, 965, "C", 200, "dpc", "LAURO"),
    RadarSite("Monte Armidda", 9.491, 39.873, 1261, "C", 200, "dpc", "ARMIDDA"),
    RadarSite("Bric della Croce", 7.733, 45.034, 727, "C", 170, "mosaic", "BRIC"),
    RadarSite("Monte Settepani", 8.197, 44.246, 1386, "C", 170, "mosaic", "SETTEPANI"),
    RadarSite("Monte Macaion", 11.210, 46.495, 1866, "C", 120, "mosaic", "MACAION"),
    RadarSite("Concordia Sagittaria", 12.786, 45.694, 0, "C", 200, "mosaic", "SAGITTARIA"),
    RadarSite("Fossalon di Grado", 13.476, 45.727, 0, "C", 125, "mosaic", "FOSSALON"),
    RadarSite("Fiumicino", 12.229, 41.905, 0, "C", 200, "mosaic", "FIUMICINO"),
    RadarSite("Linate", 9.280, 45.340, 100, "C", 250, "mosaic", "LINATE"),
    RadarSite("Capo Caccia", 8.160, 40.565, 200, "C", 200, "mosaic", "CAPOCACCIA"),
    RadarSite("Monte Rasu", 9.005, 40.421, 1258, "C", 200, "mosaic", "RASU"),
    RadarSite("Salento", 18.061, 40.502, 50, "C", 200, "mosaic"),
    RadarSite("Napoli Capodichino", 14.290, 40.884, 90, "X", 100, "mosaic", "XBAND1"),
    RadarSite("Catania Fontanarossa", 15.050, 37.466, 12, "X", 100, "mosaic", "XBAND4"),
    RadarSite("Bari Palese", 16.765, 41.136, 50, "X", 100, "mosaic", "XBAND3"),
    RadarSite("San Pietro Capofiume", 11.624, 44.655, 11, "C", 125, "literature", "CAPOFIUME"),
    RadarSite("Monte Grande", 11.673, 45.363, 472, "C", 125, "literature", "GRANDE"),
    RadarSite("Gattatico", 10.499, 44.791, 35, "C", 125, "literature", "GATTATICO"),
    RadarSite("Monte Midia", 13.177, 42.058, 1710, "C", 125, "literature", "MIDIA"),
)


@dataclass(frozen=True)
class Reliability:
    """How much to trust the radar at a point. Every part is 0-1 or ``None`` (not known)."""

    score: float | None
    visibility: float | None
    coverage: float | None
    agreement: float | None
    #: Height above the ground of the lowest beam that reaches the point, metres.
    beam_height: float | None
    #: The nearest radar and its distance in km (whatever its range).
    nearest_site: str
    distance_km: float

    @property
    def reliable(self) -> bool | None:
        """Can a reading here be trusted? ``None`` outside the map."""
        return None if self.score is None else self.score >= RELIABLE_FROM


@dataclass(frozen=True)
class ReliabilityMap:
    """The map as arrays on the 1 km grid of the mosaic (NaN: not known)."""

    score: np.ndarray
    visibility: np.ndarray
    coverage: np.ndarray
    agreement: np.ndarray
    beam_height: np.ndarray
    transform: Affine
    crs: CRS
    #: The months of radar frames ``coverage`` and ``agreement`` were built on.
    built_on: str

    def at(self, lon: float, lat: float) -> Reliability:
        """The reliability at a point; all ``None`` outside the mosaic."""
        site, distance = nearest_site(lon, lat)
        xs, ys = warp_points("EPSG:4326", self.crs, [lon], [lat])
        inv = ~self.transform  # written out: affine 2 has no ``@``, affine 3 deprecates ``*``
        col, row = inv.a * xs[0] + inv.c, inv.e * ys[0] + inv.f
        r, c = math.floor(row), math.floor(col)
        if not (0 <= r < self.score.shape[0] and 0 <= c < self.score.shape[1]):
            return Reliability(None, None, None, None, None, site.name, distance)
        parts = [
            None if math.isnan(v) else round(float(v), 2)
            for v in (a[r, c] for a in (self.score, self.visibility, self.coverage, self.agreement))
        ]
        beam = self.beam_height[r, c]
        return Reliability(
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            None if math.isnan(beam) else float(beam),
            site.name,
            distance,
        )

    def like(self, grid: RainGrid, part: Part = "score") -> np.ndarray:
        """The score (or another ``part`` of the map) on the pixels of ``grid``.

        Any product: 1 km or 0.01°. NaN where not known.
        """
        out = np.full(grid.data.shape, np.nan, dtype="float32")
        source: np.ndarray = getattr(self, part)
        reproject(
            source.astype("float32"),
            out,
            src_transform=self.transform,
            src_crs=self.crs,
            src_nodata=np.nan,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=Resampling.nearest,
        )
        return out


@cache
def reliability_map() -> ReliabilityMap:
    """The map shipped with the package (read once)."""
    with (
        as_file(files("dpc_radar") / "data" / MAP_FILE) as path,
        rasterio.open(path) as src,
    ):
        raw = src.read()
        transform, crs, built_on = src.transform, src.crs, src.tags().get("built_on", "")
    parts = {}
    for name, band in zip(BANDS, raw, strict=True):
        scale = _BEAM_STEP_M if name == "beam_height" else 0.01
        parts[name] = np.where(band == _NODATA, np.nan, band * scale)
    return ReliabilityMap(
        parts["score"],
        parts["visibility"],
        parts["coverage"],
        parts["agreement"],
        parts["beam_height"],
        transform,
        crs,
        built_on,
    )


def reliability(lon: float, lat: float) -> Reliability:
    """How much to trust the radar at a point: a score and its parts."""
    return reliability_map().at(lon, lat)


def nearest_site(lon: float, lat: float) -> tuple[RadarSite, float]:
    """The nearest radar and its distance in km along the great circle."""
    return min(((s, _km(lon, lat, s.lon, s.lat)) for s in SITES), key=lambda pair: pair[1])


def _km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(a))
