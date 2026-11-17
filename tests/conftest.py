"""Synthetic DPC frames, written as real GeoTIFFs in memory."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from affine import Affine
from rasterio.io import MemoryFile

from dpc_radar import RainGrid
from dpc_radar.client import ABSENT, Absent
from dpc_radar.products import LastProduct, Product, product
from dpc_radar.status import NetworkStatus, parse_status

#: The CUM grid: 0.01° in EPSG:4326. A 20x20 piece whose top-left is 15°E 38°N.
LONLAT = Affine(0.01, 0, 15.0, 0, -0.01, 38.0)
#: The SRI/SRT1 grid: 1 km, Transverse Mercator centred on 42°N 12.5°E, as the DPC.
TMERC = "+proj=tmerc +lat_0=42 +lon_0=12.5 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m"
METRES = Affine(1000, 0, -10_000, 0, -1000, 10_000)
T0 = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


def tiff(data: np.ndarray, transform: Affine = LONLAT, crs: str = "EPSG:4326") -> bytes:
    """``data`` (with -9999 for no data) as the bytes of a float32 GeoTIFF."""
    profile = {
        "driver": "GTiff",
        "width": data.shape[1],
        "height": data.shape[0],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": -9999.0,
    }
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(data.astype("float32"), 1)
        return bytes(mem.read())


def grid(data: np.ndarray, name: str = "CUM24", time: datetime = T0) -> RainGrid:
    return RainGrid.from_geotiff(tiff(data), name, time)


class Radar:
    """A stand-in for ``RadarClient``: it holds frames, and counts what it is asked for."""

    def __init__(self, frames: list[RainGrid], newest: dict[str, datetime] | None = None) -> None:
        self.frames = {(f.product.name, f.time): f for f in frames}
        self.newest = newest or {}
        self.asked: list[tuple[str, datetime]] = []
        self.closed = False
        self.status: NetworkStatus | Absent = ABSENT

    async def radar_status(self, time: datetime | None = None) -> NetworkStatus | Absent:
        return self.status

    async def last_product(self, name: str | Product) -> LastProduct | None:
        p = product(name)
        times = [t for n, t in self.frames if n == p.name]
        time = self.newest.get(p.name) or max(times, default=None)
        return None if time is None else LastProduct(p, time, p.period)

    async def at(self, name: str | Product, time: datetime) -> RainGrid | Absent:
        key = (product(name).name, time)
        self.asked.append(key)
        return self.frames.get(key, ABSENT)

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self) -> Radar:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def status_file(off: tuple[str, ...] = ("LAURO", "LINATE")) -> dict[str, object]:
    """A status file shaped as the DPC's: MultiPoints, the radars repeated, a stale time."""
    radars = [
        ("ILMONTE", "Monte Il Monte", "Chieti", "DPC", 14.6239, 41.9394, 692),
        ("LAURO", "Monte Lauro", "Siracusa", "DPC", 14.8239, 37.1228, 960),
        ("LINATE", "Linate", "Milano", "Enav", 9.28, 45.34, 112),
        ("XBAND4", "Aereoporto", "Catania", "DPC", 15.0498, 37.4617, 19),
        ("XBAND2", "Aereoporto", "Reggio Calabria", "DPC", 15.65, 38.07, None),
    ]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "MultiPoint", "coordinates": [[lon, lat]]},
            "properties": {
                "nome": code,
                "localita": place,
                "provincia": province,
                "gestore": operator,
                "quota": height,
                "status": code not in off,
                "time": "2025-11-16T23:55:00Z",
            },
        }
        for code, place, province, operator, lon, lat, height in radars
    ]
    return {"type": "FeatureCollection", "features": [*features, *features[:2]]}


def status(off: tuple[str, ...] = ("LAURO", "LINATE")) -> NetworkStatus:
    return parse_status(status_file(off), T0)


@pytest.fixture
def rain() -> np.ndarray:
    """20x20 of 10 mm, with one wrong pixel (7,348 mm) and a no-data corner."""
    data = np.full((20, 20), 10.0)
    data[5, 5] = 7348.0
    data[0:3, 17:20] = -9999.0
    return data
