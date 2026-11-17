from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from dpc_radar import SITES, DpcRadarError
from dpc_radar import status as s
from tests.conftest import T0, status, status_file


def test_the_address_is_of_the_five_minutes_the_time_falls_in() -> None:
    late = datetime(2026, 11, 17, 14, 7, 31, tzinfo=timezone(timedelta(hours=1)))
    assert s.status_time(late) == datetime(2026, 11, 17, 13, 5, tzinfo=UTC)
    assert s.status_url(late) == f"{s.STATUS_BASE}/2026/11/17/1305/sites.geojson"
    assert s.status_url(T0, base="http://x/") == "http://x/2026/10/10/0600/sites.geojson"
    with pytest.raises(DpcRadarError, match="needs its zone"):
        s.status_url(datetime(2026, 11, 17, 13, 5))


def test_the_radars_of_a_file_once_each_with_the_time_asked_for() -> None:
    found = status()
    assert found.time == T0  # not the stale time inside the file
    assert [r.code for r in found.radars] == ["ILMONTE", "LAURO", "LINATE", "XBAND4", "XBAND2"]
    assert [r.code for r in found.off] == ["LAURO", "LINATE"]
    lauro = found.radars[1]
    assert (lauro.place, lauro.province, lauro.operator) == ("Monte Lauro", "Siracusa", "DPC")
    assert (lauro.lon, lauro.lat, lauro.altitude, lauro.on) == (14.8239, 37.1228, 960.0, False)
    assert lauro.site is not None and lauro.site.name == "Monte Lauro"
    reggio = found.radars[-1]  # in the file, not in the mosaic
    assert reggio.site is None and reggio.altitude is None and reggio.on


def test_is_a_radar_on() -> None:
    found = status()
    lauro = next(site for site in SITES if site.name == "Monte Lauro")
    salento = next(site for site in SITES if site.name == "Salento")
    assert found.of(lauro) is False and found.of("Monte Lauro") is False
    assert found.of("ILMONTE") is True and found.of("Il Monte") is True
    assert found.of(salento) is None and found.of("Salento") is None  # not in the file
    assert found.of("Monte Serano") is None  # not in this file


def test_every_site_but_salento_has_a_code_of_its_own() -> None:
    codes = [site.code for site in SITES if site.code is not None]
    assert len(codes) == len(set(codes)) == len(SITES) - 1


def test_as_geojson() -> None:
    out = status().geojson()
    assert out["type"] == "FeatureCollection" and out["time"] == "2026-10-10T06:00Z"
    assert out["note"] == s.STATUS_CAVEAT
    first, last = out["features"][0], out["features"][-1]
    assert first["geometry"] == {"type": "Point", "coordinates": [14.6239, 41.9394]}
    assert first["properties"]["name"] == "Il Monte" and first["properties"]["on"] is True
    assert last["properties"]["name"] is None and last["properties"]["in_mosaic"] is False


def test_a_plain_point_is_read_too() -> None:
    file = status_file()
    features: Any = file["features"]
    features[0]["geometry"] = {"type": "Point", "coordinates": [14.6, 41.9]}
    assert s.parse_status(file, T0).radars[0].lon == 14.6


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"features": []},
        {"features": "none"},
        {"features": [{"properties": {"nome": "X"}}]},
        {"features": [{"properties": {"nome": "X", "status": True}, "geometry": None}]},
        {
            "features": [
                {
                    "properties": {"nome": "X", "status": True},
                    "geometry": {"type": "MultiPoint", "coordinates": []},
                }
            ]
        },
    ],
)
def test_a_file_that_is_not_the_one_we_know(payload: dict[str, Any]) -> None:
    with pytest.raises(DpcRadarError, match="radar status file"):
        s.parse_status(payload, T0)
