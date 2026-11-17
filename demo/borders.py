"""Country outlines for the maps of the demo: Natural Earth, public domain.

    python demo/borders.py data/cache      # download once, ahead of time

They are not in the package (a library does not need them), so the demo
downloads them the first time and keeps them in its cache folder.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import numpy as np

URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_50m_admin_0_countries.geojson"
)
#: Only what can show on a map of Italy: west, south, east, north.
AROUND_ITALY = (3.0, 34.0, 22.0, 49.5)


def fetch(cache: Path) -> Path:
    """The file of the outlines in ``cache``, downloaded if it is not there yet."""
    file = cache / "countries.geojson"
    if not file.exists():
        cache.mkdir(parents=True, exist_ok=True)
        answer = httpx.get(URL, timeout=120, follow_redirects=True)
        answer.raise_for_status()
        part = file.with_suffix(".part")
        part.write_bytes(answer.content)
        part.rename(file)
    return file


def rings(cache: Path) -> list[np.ndarray]:
    """The outlines around Italy, each an array of lon/lat points."""
    west, south, east, north = AROUND_ITALY
    found = []
    for feature in json.loads(fetch(cache).read_text())["features"]:
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for polygon in polygons:
            ring = np.array(polygon[0])
            lons, lats = ring[:, 0], ring[:, 1]
            if (
                lons.max() > west
                and lons.min() < east
                and lats.max() > south
                and lats.min() < north
            ):
                found.append(ring)
    return found


if __name__ == "__main__":
    print(fetch(Path(sys.argv[1] if len(sys.argv) > 1 else "data/cache")))
