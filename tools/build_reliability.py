"""Build the reliability map shipped with the package (``dpc_radar/data/reliability.tif``).

    uv run python -m tools.build_reliability --archive <folder of archive()> --dem <folder>

It needs a local archive of SRT1 and CUM3 frames (:func:`dpc_radar.archive.archive`)
and downloads once, into ``--dem``, the open terrain tiles it reads the mountains
from (Terrarium, Mapzen/AWS Open Data, zoom 7: about 900 m per pixel at 42°N).

The parts (see :mod:`dpc_radar.reliability`):

* visibility: for every radar, along 720 directions, the lowest beam that
  clears the terrain (4/3 Earth radius, lowest scan at 0.5°, antenna 20 m above
  the ground); its height above the ground at every pixel within the radar's
  range; the lowest over the radars;
* coverage: the share of the SRT1 frames with data at the pixel (under 5 %: not
  in the mosaic);
* agreement: the sum of SRT1 against the sum of CUM3 over the same three hours,
  both smoothed on 9 km, as the smaller of the two ratios, where the «Merging»
  measured at least ``MIN_RAIN_MM`` on land. The sums are those of
  ``tools/build_bias.py``: a window with a rain gauge gone wrong (the
  ``gauge_only`` rule) is left out within 15 km of it, and where a gauge reports
  rain under a dry radar most of the time the agreement is not known. Such a
  gauge says nothing of the radar, and before this it made the radar look
  blind around Foggia. ``--cache`` shares the file of those sums with the bias
  build; the bias build reads the visibility and the coverage of the map
  already shipped, which the agreement does not change.

``--skip`` leaves out a period in which the cumulates are known to be wrong.
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject
from rasterio.warp import transform as warp_points

from dpc_radar.archive import MergingArchive
from dpc_radar.reliability import _BEAM_STEP_M, _NODATA, BANDS, MAP_FILE, SITES

TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/7/{x}/{y}.png"
TILE_X, TILE_Y = range(64, 72), range(42, 52)
SHAPE = (1400, 1200)
TRANSFORM = Affine(1000.0, 0.0, -600000.0, 0.0, -1000.0, 650000.0)
CRS_TM = CRS.from_proj4("+proj=tmerc +lat_0=42 +lon_0=12.5 +k=1 +datum=WGS84 +units=m")
EARTH_M = 6371000.0 * 4 / 3  # standard refraction
LOWEST_SCAN = np.radians(0.5)
TOWER_M = 20.0
FULL_BELOW_M, NONE_ABOVE_M = 1500.0, 5000.0
MIN_RAIN_MM = 50.0
SMOOTH = 9
MIN_COVERAGE = 0.05  # data in fewer frames than this: a stray frame, not the mosaic


def dem(folder: Path) -> np.ndarray:
    """Terrain height in metres on the mosaic grid (sea: 0)."""
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for y in TILE_Y:
        row = []
        for x in TILE_X:
            path = folder / f"{x}_{y}.png"
            if not path.exists():
                path.write_bytes(httpx.get(TILES.format(x=x, y=y), timeout=60).content)
            with rasterio.open(path) as src:
                r, g, b = src.read()[:3].astype("float32")
            row.append(r * 256 + g + b / 256 - 32768)
        rows.append(np.hstack(row))
    mosaic = np.vstack(rows)
    world = 20037508.342789244
    size = 2 * world / 2**7 / 256
    src_transform = Affine(
        size, 0, -world + TILE_X[0] * 256 * size, 0, -size, world - TILE_Y[0] * 256 * size
    )
    out = np.zeros(SHAPE, dtype="float32")
    reproject(
        mosaic,
        out,
        src_transform=src_transform,
        src_crs="EPSG:3857",
        dst_transform=TRANSFORM,
        dst_crs=CRS_TM,
        resampling=Resampling.bilinear,
    )
    return np.maximum(out, 0)


def beam_height(terrain: np.ndarray) -> np.ndarray:
    """Height above the ground of the lowest unblocked beam over all radars (NaN: none)."""
    best = np.full(SHAPE, np.inf, dtype="float32")
    rows, cols = np.indices(SHAPE)
    az = np.radians(np.arange(0, 360, 0.5))
    for site in SITES:
        xs, ys = warp_points("EPSG:4326", CRS_TM, [site.lon], [site.lat])
        c0, r0 = (xs[0] + 600000) / 1000, (650000 - ys[0]) / 1000
        # The antenna: on a tower, and never under the terrain model's ground.
        here = terrain[int(np.clip(r0, 0, SHAPE[0] - 1)), int(np.clip(c0, 0, SHAPE[1] - 1))]
        antenna = max(site.altitude, float(here)) + TOWER_M
        rng = np.arange(1, int(site.range_km) + 1)
        pr = np.clip(np.rint(r0 - 0.5 - rng[None, :] * np.cos(az)[:, None]), 0, SHAPE[0] - 1)
        pc = np.clip(np.rint(c0 - 0.5 + rng[None, :] * np.sin(az)[:, None]), 0, SHAPE[1] - 1)
        ground = terrain[pr.astype(int), pc.astype(int)]
        metres = rng[None, :] * 1000.0
        angle = (ground - antenna) / metres - metres / (2 * EARTH_M)
        clear = np.maximum(np.maximum.accumulate(angle, axis=1), LOWEST_SCAN)
        dist = np.hypot(rows + 0.5 - r0, cols + 0.5 - c0)
        inside = (dist <= site.range_km) & (dist >= 0.5)
        d = dist[inside]
        bearing = np.arctan2(cols[inside] + 0.5 - c0, r0 - rows[inside] - 0.5) % (2 * np.pi)
        ia = np.rint(bearing / np.radians(0.5)).astype(int) % len(az)
        ir = np.clip(np.rint(d).astype(int) - 1, 0, len(rng) - 1)
        dm = d * 1000.0
        height = antenna + dm * clear[ia, ir] + dm**2 / (2 * EARTH_M) - terrain[inside]
        best[inside] = np.minimum(best[inside], np.maximum(height, 0))
    best[np.isinf(best)] = np.nan
    return best


def box(a: np.ndarray, size: int) -> np.ndarray:
    """Mean over a ``size`` x ``size`` window (zero outside)."""
    pad = size // 2
    s = np.pad(a, pad).cumsum(0).cumsum(1)
    s = np.pad(s, ((1, 0), (1, 0)))
    return (s[size:, size:] - s[:-size, size:] - s[size:, :-size] + s[:-size, :-size]) / size**2


def stored(folder: Path, product: str) -> list[datetime]:
    """The times of the frames of ``product`` on disk, oldest first."""
    names = sorted(p.stem for p in (folder / product).glob("*/*/*/*.tif"))
    return [datetime.strptime(n.split("_")[1], "%Y%m%d%H%M").replace(tzinfo=UTC) for n in names]


def frame(arc: MergingArchive, product: str, time: datetime) -> np.ndarray:
    grid = arc.read(product, time)
    return np.full(SHAPE, np.nan) if grid is None else grid.data


def coverage_of(folder: Path) -> tuple[np.ndarray, str]:
    """The share of the SRT1 frames with data at every pixel, and the days they span."""
    arc = MergingArchive(folder, cache_frames=8)
    hours = stored(folder, "SRT1")
    seen = np.zeros(SHAPE, dtype="float64")
    for t in hours:
        seen += np.isfinite(frame(arc, "SRT1", t))
    return seen / len(hours), f"{hours[0]:%Y-%m-%d}/{hours[-1]:%Y-%m-%d}"


def agreement_of(
    folder: Path, terrain: np.ndarray, skip: tuple[date, date] | None, cache: Path | None
) -> tuple[np.ndarray, np.ndarray]:
    """The agreement of SRT1 with CUM3, and where a chronic gauge fault leaves it unknown."""
    from tools.build_bias import chronic, load, total  # it imports this module

    sums = load(folder, cache, skip)
    months = sorted(sums)
    bad = chronic(sums, months)
    r = box(np.where(bad, 0, total(sums, "radar", months)), SMOOTH)
    m = box(np.where(bad, 0, total(sums, "merged", months)), SMOOTH)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = r / m
        agreement = np.minimum(ratio, 1 / ratio)
    sea = box((terrain > 0).astype(float), SMOOTH) < 0.5
    agreement[(m < MIN_RAIN_MM) | sea | bad] = np.nan
    return agreement, bad


def build(
    archive: Path,
    dem_folder: Path,
    skip: tuple[date, date] | None,
    cache: Path | None = None,
) -> dict[str, np.ndarray]:
    terrain = dem(dem_folder)
    beam = beam_height(terrain)
    visibility = np.clip((NONE_ABOVE_M - beam) / (NONE_ABOVE_M - FULL_BELOW_M), 0, 1)
    coverage, built_on = coverage_of(archive)
    agreement, bad = agreement_of(archive, terrain, skip, cache)
    in_mosaic = coverage >= MIN_COVERAGE
    for part in (visibility, agreement, beam):
        part[~in_mosaic] = np.nan
    coverage = np.where(in_mosaic, coverage, 0.0)  # never seen: the lowest there is
    visibility[in_mosaic & np.isnan(visibility)] = 0.0  # data from a radar we do not know
    score = np.fmin(np.fmin(visibility, coverage), agreement)
    return {
        "score": score,
        "visibility": visibility,
        "coverage": coverage,
        "agreement": agreement,
        "beam_height": beam,
        "built_on": np.array(built_on),
        "gauge_faults": bad,
    }


def write(parts: dict[str, np.ndarray], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out,
        "w",
        driver="GTiff",
        height=SHAPE[0],
        width=SHAPE[1],
        count=len(BANDS),
        dtype="uint8",
        crs=CRS_TM,
        transform=TRANSFORM,
        nodata=_NODATA,
        compress="deflate",
        predictor=2,
        zlevel=9,
        tiled=True,
    ) as dst:
        for i, name in enumerate(BANDS, start=1):
            a = parts[name]
            scaled = np.minimum(a / _BEAM_STEP_M, 254) if name == "beam_height" else a * 100
            dst.write(np.where(np.isnan(a), _NODATA, np.rint(scaled)).astype("uint8"), i)
            dst.set_band_description(i, name)
        dst.update_tags(
            built_on=str(parts["built_on"]),
            built_at=f"{datetime.now(UTC):%Y-%m-%d}",
            source="DPC radar mosaic, CC BY-SA 4.0; Terrarium terrain tiles, Mapzen on AWS",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--dem", type=Path, required=True)
    parser.add_argument("--skip", nargs=2, type=date.fromisoformat, metavar=("FROM", "TO"))
    parser.add_argument("--cache", type=Path, help="the sums of tools/build_bias.py, kept here")
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parents[1] / "src/dpc_radar/data" / MAP_FILE
    )
    args = parser.parse_args()
    parts = build(args.archive, args.dem, tuple(args.skip) if args.skip else None, args.cache)
    write(parts, args.out)
    print(
        f"{args.out}: {args.out.stat().st_size / 1024:.0f} kB, built on {parts['built_on']}, "
        f"agreement left unknown on {int(parts['gauge_faults'].sum())} km² of gauge faults"
    )


if __name__ == "__main__":
    main()
