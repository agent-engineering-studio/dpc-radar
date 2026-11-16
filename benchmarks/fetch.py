"""Download the frames of every case from the DPC API and crop them.

    uv run --group bench python -m benchmarks.fetch [--cache DIR] [--force]

For each case: the frame at ``time`` and the one before it, downloaded with
dpc-radar's own requests, cropped to ``bbox`` and written to
``frames/<id>/frame.tif`` and ``previous.tif`` (float32, -9999 as no data,
deflate). A case with ``radar = true`` also gets ``radar.tif``: the radar-only
rain over the same window, the sum of its hourly SRT1 frames. Full frames are
kept in the cache (default ``.cache/dpc-radar``), so a second run is offline.
Cases already on disk are skipped unless ``--force``.

The DPC keeps frames for about five months: a case added later than that can no
longer be fetched. That is why the cropped frames are committed.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from benchmarks.bench import Case, load_cases
from dpc_radar import is_expired, parse_download, product, products


async def download(client: httpx.AsyncClient, name: str, at: datetime, cache: Path) -> Path:
    path = cache / name / f"{name}_{at:%Y%m%d%H%M}.tif"
    if path.exists():
        return path
    for attempt in range(4):  # the API sometimes drops a connection
        try:
            r = await client.post(
                products.download_url(),
                json=products.download_body(product(name), at),
                headers=products.HEADERS,
            )
            if r.status_code != 200:
                if is_expired(r.status_code, r.text):
                    raise SystemExit(f"{name} {at}: the DPC no longer keeps this frame")
                r.raise_for_status()
            tif = await client.get(parse_download(r.json()))
            tif.raise_for_status()
            break
        except httpx.TransportError:
            if attempt == 3:
                raise
            await asyncio.sleep(2 * (attempt + 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tif.content)
    return path


def crop(src_path: Path, bbox: tuple[float, float, float, float], out: Path) -> None:
    with rasterio.open(src_path) as src:
        # the «Merging» is in degrees, the radar-only products in metres
        bounds = transform_bounds("EPSG:4326", src.crs, *bbox)
        window = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
        data = src.read(1, window=window)
        profile = {
            "driver": "GTiff",
            "width": data.shape[1],
            "height": data.shape[0],
            "count": 1,
            "dtype": "float32",
            "crs": src.crs,
            "transform": src.window_transform(window),
            "nodata": -9999.0,
            "compress": "deflate",
            "predictor": 3,
            "zlevel": 9,
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data.astype("float32"), 1)
        dst.update_tags(SOURCE="Dipartimento della Protezione Civile, CC BY-SA 4.0")


def total(paths: list[Path], out: Path) -> Path:
    """The sum of whole frames on one grid, no data where any of them has none."""
    with rasterio.open(paths[0]) as src:
        profile = src.profile
    frames = []
    for p in paths:
        with rasterio.open(p) as src:
            frames.append(src.read(1).astype("float32"))
    stack = np.stack(frames)
    data = np.where((stack < 0).any(axis=0), -9999.0, stack.sum(axis=0))
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data.astype(profile["dtype"]), 1)
    return out


async def fetch(cases: list[Case], cache: Path, force: bool) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        for case in cases:
            frame_out, previous_out = case.paths()
            if not (frame_out.exists() and previous_out.exists()) or force:
                for at, out in ((case.time, frame_out), (case.previous_time, previous_out)):
                    full = await download(client, case.product, at, cache)
                    crop(full, case.bbox, out)
                print(
                    f"{case.id}: {frame_out.stat().st_size // 1024} + "
                    f"{previous_out.stat().st_size // 1024} KB"
                )
            if case.radar and (not case.radar_path.exists() or force):
                hours = product(case.product).hours or 1
                hourly = [
                    await download(client, "SRT1", case.time - timedelta(hours=k), cache)
                    for k in range(hours)
                ]
                whole = total(hourly, cache / "radar" / f"{case.id}.tif")
                crop(whole, case.bbox, case.radar_path)
                print(
                    f"{case.id}: radar over {hours} h, {case.radar_path.stat().st_size // 1024} KB"
                )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.fetch")
    parser.add_argument("--cache", type=Path, default=Path(".cache/dpc-radar"))
    parser.add_argument("--force", action="store_true", help="crop again cases already on disk")
    args = parser.parse_args()
    asyncio.run(fetch(load_cases(), args.cache, args.force))


if __name__ == "__main__":
    main()
