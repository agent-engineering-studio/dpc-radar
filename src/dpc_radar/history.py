"""Fifteen years of radar rain: the IT-DPC-SRI archive (2010-2025), read from disk.

IT-DPC-SRI (Fondazione Bruno Kessler on DPC data, CC BY-SA 4.0,
`doi:10.5281/zenodo.18637608 <https://doi.org/10.5281/zenodo.18637608>`_) is the
national SRI mosaic, rain intensity in mm/h from the radar alone, as one
Zarr v2 store: one chunk per time step, 1400 x 1200 pixels of 1 km in a
Transverse Mercator projection centred on 42°N 12.5°E, a frame every 15 minutes
until 2014, every 10 until 2020, every 5 after. It is published as a single
50 GB ``.zarr.tar.zst``, served at about 0.1 MB/s: days. The same store is on
ECMWF's object store (catalogue mlcast-datasets), anonymous, a frame in about
0.14 s: :func:`mirror` copies it file by file in a few hours, and can stop and
start again. Point :class:`SriHistory` at the ``.zarr`` folder, either one; with
no folder it reads ``it_dpc_sri/`` in the library's folder, where
``dpc_radar.local.init(history=True)`` puts the mirror.

It is read here without zarr or xarray: the Blosc codec (extra ``history``,
``pip install "dpc-radar[history]"``) is enough to decompress a frame.

It is the history the API does not give (it keeps only the last months), and it
is radar only: it under-estimates heavy rain, so compare it with the «Merging»
of :mod:`dpc_radar.archive` where both exist.

An hour covered for less than half by frames is ``None``, not measured: a gap
in the archive is not a dry hour.

Fewer than one frame in a thousand is corrupted (752 of 1,039,785): the whole
disk of a radar filled with violent rain, or flat patches of one value over
100 mm/h (229 frames in 2011, 250 in 2014, 5 to 44 in the other years).
:func:`suspect` tells them from what rain looks like in the other million
frames, and :meth:`SriHistory.hourly` reads them as missing. It does not catch
everything: a single spike or a short ray of clutter passes, and the median of
the 3x3 pixels is what softens those.

It is not one series. Until 2017 the mosaic read less: the highest hour of the
year is about two thirds of what it is since 2018 (the table is in
:mod:`dpc_radar.climatology`). A threshold, a trend or a statistic that
crosses 2018 compares two different instruments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import warnings
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import httpx
import numpy as np
from rasterio.warp import transform as warp_points

from dpc_radar.archive import ArchiveReport
from dpc_radar.errors import DpcRadarError
from dpc_radar.local import history_directory

_log = logging.getLogger(__name__)

#: The origin of the store's time axis ("minutes since 2010-01-01").
EPOCH: Final = datetime(2010, 1, 1, tzinfo=UTC)
#: The longest step of the archive: a wider interval is a gap, and the frame
#: before the gap does not cover it.
_MAX_STEP_MIN = 15


#: A frame is suspect with at least this many pixels of violent rain (100 mm/h) ...
_MIN_VIOLENT: Final = 100
#: ... when they are more than this share of the pixels with rain (1 mm/h),
_VIOLENT_OF_WET: Final = 0.05
#: or more than this share of the pixels with heavy rain (50 mm/h),
_VIOLENT_OF_HEAVY: Final = 0.7
#: or more than this many in all.
_MAX_VIOLENT: Final = 2000
#: Or with this many pixels of heavy rain, when they are more than this share of the wet ones.
_MIN_HEAVY: Final = 200
_HEAVY_OF_WET: Final = 0.2


def suspect(frame: np.ndarray) -> bool:
    """Whether a frame in mm/h looks corrupted rather than rained on.

    In a storm the violent rain is a few pixels inside a much wider area of
    lighter rain. A frame is suspect when that is not so: at least 100 pixels
    at 100 mm/h or more that are over 5 % of the pixels with rain or over 70 %
    of those at 50 mm/h (a flat patch), or more than 2000 of them; or at least
    200 pixels at 50 mm/h that are over 20 % of the pixels with rain. The
    thresholds stand on the one million frames of IT-DPC-SRI: they reject
    about one in a thousand.
    """
    heavy = int(np.count_nonzero(frame >= 50))
    if heavy < _MIN_VIOLENT:
        return False
    wet = int(np.count_nonzero(frame >= 1))
    violent = int(np.count_nonzero(frame >= 100))
    if heavy >= _MIN_HEAVY and heavy > _HEAVY_OF_WET * wet:
        return True
    if violent < _MIN_VIOLENT:
        return False
    return (
        violent > _MAX_VIOLENT
        or violent > _VIOLENT_OF_WET * wet
        or violent > _VIOLENT_OF_HEAVY * heavy
    )


def _blosc() -> Any:
    try:
        from numcodecs import Blosc
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DpcRadarError(
            'reading IT-DPC-SRI needs the Blosc codec: pip install "dpc-radar[history]"'
        ) from exc
    return Blosc()


class SriHistory:
    """Hourly rain at lon/lat points from the IT-DPC-SRI Zarr store on disk.

    Without ``path``, ``it_dpc_sri/`` in the library's folder.
    """

    def __init__(self, path: Path | None = None) -> None:
        path = path if path is not None else history_directory()
        self.path = path
        self._codec = _blosc()
        try:
            rr = json.loads((path / "RR" / ".zarray").read_text())
            crs = json.loads((path / "crs" / ".zattrs").read_text())
        except FileNotFoundError as exc:
            raise DpcRadarError(f"{path} is not the IT-DPC-SRI .zarr folder") from exc
        self.shape: tuple[int, int] = (int(rr["shape"][1]), int(rr["shape"][2]))
        x0, dx, _, y0, _, dy = (float(v) for v in crs["GeoTransform"].split())
        self._x0, self._dx, self._y0, self._dy = x0, dx, y0, dy
        self._crs_wkt: str = crs["crs_wkt"]
        self._minutes = np.frombuffer(self._decode(path / "time" / "0"), dtype="<i8")

    @property
    def first(self) -> datetime:
        return EPOCH + timedelta(minutes=int(self._minutes[0]))

    @property
    def last(self) -> datetime:
        return EPOCH + timedelta(minutes=int(self._minutes[-1]))

    def __len__(self) -> int:
        return len(self._minutes)

    def _decode(self, file: Path) -> bytes:
        return bytes(self._codec.decode(file.read_bytes()))

    def pixel(self, points: list[tuple[float, float]]) -> list[tuple[int, int] | None]:
        """(row, column) of each lon/lat point; ``None`` outside the grid."""
        xs, ys = warp_points(
            "EPSG:4326", self._crs_wkt, [p[0] for p in points], [p[1] for p in points]
        )
        out: list[tuple[int, int] | None] = []
        for x, y in zip(xs, ys, strict=True):
            r = int((y - self._y0) // self._dy)
            c = int((x - self._x0) // self._dx)
            inside = 0 <= r < self.shape[0] and 0 <= c < self.shape[1]
            out.append((r, c) if inside else None)
        return out

    def frame(self, i: int) -> np.ndarray | None:
        """Frame ``i`` of the store in mm/h (NaN for no data); ``None`` if its chunk is missing."""
        file = self.path / "RR" / f"{i}.0.0"
        if not file.exists():
            return None
        data = np.frombuffer(self._decode(file), dtype="<f4").reshape(self.shape)
        return np.where(data >= 0, data, np.nan)

    def time(self, i: int) -> datetime:
        """The time of frame ``i``."""
        return EPOCH + timedelta(minutes=int(self._minutes[i]))

    def _windows(self, pixels: list[tuple[int, int] | None]) -> tuple[np.ndarray, np.ndarray]:
        """Indices of the 3x3 pixels around each point, for one vectorised read.

        At the edges the index folds back onto the edge; a point outside the grid
        reads pixel (0, 0), and the caller drops it.
        """
        h, w = self.shape
        rows = np.zeros((len(pixels), 9), dtype=np.intp)
        cols = np.zeros((len(pixels), 9), dtype=np.intp)
        dr, dc = np.meshgrid([-1, 0, 1], [-1, 0, 1], indexing="ij")
        for k, rc in enumerate(pixels):
            if rc is None:
                continue
            r, c = rc
            rows[k] = np.clip(r + dr.ravel(), 0, h - 1)
            cols[k] = np.clip(c + dc.ravel(), 0, w - 1)
        return rows, cols

    def hourly(
        self, points: list[tuple[float, float]], start: datetime, end: datetime
    ) -> dict[datetime, list[float | None]]:
        """Rain of every hour ``(h-1, h]`` in ``(start, end]``, per lon/lat point (mm).

        The mean of the intensities of the hour's frames, each weighted by the
        time it covers until the next one (15 minutes at most), read as the
        median of the 3x3 pixels around the point. A corrupted frame
        (:func:`suspect`) is read as missing. The same signature as
        :meth:`dpc_radar.archive.MergingArchive.hourly`: the two are
        interchangeable.
        """
        pixels = self.pixel(points)
        outside = np.array([rc is None for rc in pixels])
        rows, cols = self._windows(pixels)
        a = int((start - EPOCH).total_seconds() // 60)
        b = int((end - EPOCH).total_seconds() // 60)
        # A frame at `m` is the rain from `m` to the next frame: the hours in
        # (start, end] are made of the frames issued in [start, end).
        lo = int(np.searchsorted(self._minutes, a, side="left"))
        hi = int(np.searchsorted(self._minutes, b, side="left"))
        sums: dict[int, np.ndarray] = {}
        covered: dict[int, np.ndarray] = {}
        for i in range(lo, hi):
            m = int(self._minutes[i])
            after = int(self._minutes[i + 1]) if i + 1 < len(self._minutes) else m + 5
            weight = min(after - m, _MAX_STEP_MIN)
            f = self.frame(i)
            if f is None or suspect(f):
                continue
            windows = f[rows, cols].astype(np.float64)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN window
                values = np.nanmedian(windows, axis=1)
            values[outside] = np.nan
            # A frame counts from `m` to the next one: it falls in the hour closing after it.
            hour = m // 60 + 1
            ok = np.isfinite(values)
            sums.setdefault(hour, np.zeros(len(points)))[ok] += values[ok] * weight
            covered.setdefault(hour, np.zeros(len(points)))[ok] += weight
        out: dict[datetime, list[float | None]] = {}
        for hour, total in sorted(sums.items()):
            cov = covered[hour]
            out[EPOCH + timedelta(hours=hour)] = [
                float(s / c) if c >= 30 else None for s, c in zip(total, cov, strict=True)
            ]
        return out


#: The IT-DPC-SRI store on ECMWF's object store: the same Zarr as on Zenodo, file by file.
SRI_STORE: Final = (
    "https://object-store.os-api.cci2.ecmwf.int/mlcast-source-datasets/"
    "IT-DPC-SRI/v0.1.0/italian-radar-dpc-sri.zarr"
)
#: The arrays besides RR: small, one chunk each.
_COORDINATES: Final = ("crs", "lat", "lon", "x", "y", "time", "missing_times")
_GONE: Final = frozenset({403, 404})  # a hole in the store: the object store says 403
_RETRY_STATUS: Final = frozenset({429, 500, 502, 503, 504})
#: How many frames are scheduled at once: a million tasks would hold a million coroutines.
_BATCH: Final = 5000


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    retries: int,
    backoff_s: float,
    sleep: Callable[[float], Awaitable[Any]],
) -> bytes | None:
    """The body of ``url``; ``None`` if the store does not have it."""
    for attempt in range(retries + 1):
        try:
            response = await client.get(url)
        except httpx.TransportError:
            if attempt == retries:
                raise
        else:
            if response.status_code in _GONE:
                return None
            if response.status_code not in _RETRY_STATUS or attempt == retries:
                response.raise_for_status()
                return response.content
        await sleep(backoff_s * 2**attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def _write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.rename(dest)


async def mirror(
    directory: Path,
    client: httpx.AsyncClient | None = None,
    *,
    retries: int = 3,
    concurrency: int = 32,
    start: int = 0,
    stop: int | None = None,
    base: str = SRI_STORE,
    backoff_s: float = 2.0,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> ArchiveReport:
    """Copy the IT-DPC-SRI store from ECMWF's object store into ``directory``.

    The metadata and the coordinates first, then the frames ``start`` to
    ``stop`` (indices on the time axis: 179087 is the first of 2015),
    ``concurrency`` at a time. Safe to stop and run again: a file on disk is
    not downloaded twice, and a frame the store does not have (a 403 or 404)
    is written to ``missing.txt`` and not asked again. A file that fails is
    counted, logged and retried on the next run; a disk that cannot be written
    raises. ``client`` is used and left open; ``retries=0`` leaves the
    retrying to the caller.
    """
    report = ArchiveReport()
    own = client is None
    http = client or httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=concurrency))
    base = base.rstrip("/")

    async def one(key: str, dest: Path) -> str:
        """Download ``key`` to ``dest`` unless it is there: ``there``, ``gone`` or ``failed``."""
        if dest.exists():
            report.present += 1
            return "there"
        try:
            data = await _fetch(http, f"{base}/{key}", retries, backoff_s, sleep)
        except httpx.HTTPError as exc:
            _log.warning("IT-DPC-SRI %s failed: %s", key, type(exc).__name__)
            report.failed += 1
            return "failed"
        if data is None:
            report.lost += 1
            return "gone"
        _write(dest, data)
        report.downloaded += 1
        report.bytes += len(data)
        return "there"

    try:
        directory.mkdir(parents=True, exist_ok=True)
        if await one(".zmetadata", directory / ".zmetadata") != "there":
            return report
        for key in (".zgroup", ".zattrs"):
            await one(key, directory / key)
        meta: dict[str, dict[str, Any]] = json.loads((directory / ".zmetadata").read_text())[
            "metadata"
        ]
        for key, value in meta.items():
            if key.endswith((".zarray", ".zattrs")) and not (directory / key).exists():
                _write(directory / key, json.dumps(value).encode())
        for name in _COORDINATES:
            if f"{name}/.zarray" in meta:
                shape = meta[f"{name}/.zarray"]["shape"]
                chunk = ".".join("0" for _ in shape) if shape else "0"
                await one(f"{name}/{chunk}", directory / name / chunk)

        rr = directory / "RR"
        rr.mkdir(exist_ok=True)
        missing_file = directory / "missing.txt"
        missing = set(missing_file.read_text().split()) if missing_file.exists() else set()
        on_disk = {p.name for p in rr.iterdir()}
        end = stop if stop is not None else int(meta["RR/.zarray"]["shape"][0])
        todo = []
        for i in range(start, end):
            if f"{i}.0.0" in on_disk or str(i) in missing:
                report.present += 1
            else:
                todo.append(i)
        _log.info("IT-DPC-SRI frames %d-%d: %d to download", start, end, len(todo))
        gate = asyncio.Semaphore(concurrency)

        async def frame(i: int) -> None:
            async with gate:
                got = await one(f"RR/{i}.0.0", rr / f"{i}.0.0")
            if got == "gone":
                with missing_file.open("a") as f:
                    f.write(f"{i}\n")

        for k in range(0, len(todo), _BATCH):
            await asyncio.gather(*(frame(i) for i in todo[k : k + _BATCH]))
            done = min(k + _BATCH, len(todo))
            _log.info("IT-DPC-SRI: %d of %d, %.1f GB", done, len(todo), report.bytes / 1e9)
    finally:
        if own:
            await http.aclose()
    return report


__all__ = ["EPOCH", "SRI_STORE", "SriHistory", "mirror", "suspect"]
