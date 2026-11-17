from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dpc_radar import DpcRadarError, cli
from dpc_radar import report as r
from tests.conftest import Radar
from tests.test_facts import bulletin
from tests.test_report import frames

BOX = "15.02,37.82,15.18,37.98"


class Archive:
    """A stand-in for ``MergingArchive``: the frames of the test day, whatever the folder."""

    def __init__(self, folder: Path) -> None:
        self.read = lambda name, time: frames().get((name, time))


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> list[date]:
    """No network: the archive, the API and the bulletins are stand-ins. The days asked for."""
    asked: list[date] = []

    async def found(day: date, **_: Any) -> list[Any]:
        asked.append(day)
        return [bulletin()]

    monkeypatch.setattr("dpc_radar.archive.MergingArchive", Archive)
    monkeypatch.setattr("dpc_radar.RadarClient", lambda: Radar(list(frames().values())))
    monkeypatch.setattr("dpc_radar.report.bulletins_for", found)
    return asked


def test_a_report_from_an_archive(
    offline: list[date], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "melito"
    args = ["report", "--bbox", BOX, "--day", "2026-10-10", "--out", str(out)]
    assert cli.main([*args, "--archive", str(tmp_path)]) == 0
    written = capsys.readouterr().out.split()
    assert [Path(p).name for p in written] == ["facts.json", "report.md", "map.png", "hourly.png"]
    assert offline == [date(2026, 10, 10)]
    facts = json.loads((out / "facts.json").read_text())
    assert facts["bulletins"][0]["zone"]["colour"] == "orange"

    assert cli.main(["check", str(out / "report.md"), str(out / "facts.json")]) == 0
    assert "every number of the text is in the facts" in capsys.readouterr().out


def test_a_report_from_the_api_with_a_text_of_ones_own(
    offline: list[date], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = tmp_path / "mine.md"
    text.write_text("On 10 October 2026 the «Merging» counted 118.0 mm.")
    args = ["report", "--bbox", BOX, "--day", "2026-10-10", "--out", str(tmp_path / "out")]
    assert cli.main([*args, "--no-bulletin", "--no-images", "--text", str(text)]) == 0
    assert len(capsys.readouterr().out.split()) == 2 and offline == []
    assert (tmp_path / "out" / "report.md").read_text() == text.read_text()
    assert json.loads((tmp_path / "out" / "facts.json").read_text())["bulletins"] == []

    text.write_text("It rained 250 mm.")
    assert cli.main([*args, "--no-bulletin", "--text", str(text)]) == 1
    assert "dpc-radar: the text holds numbers that are not in the facts: 250" in (
        capsys.readouterr().err
    )

    assert cli.main(["check", str(text), str(tmp_path / "out" / "facts.json")]) == 1
    assert "numbers that are not in the facts: 250" in capsys.readouterr().err


def test_what_goes_wrong_is_said_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["check", str(tmp_path / "none.md"), str(tmp_path / "none.json")]) == 1
    assert capsys.readouterr().err.startswith("dpc-radar: ")
    args = ["report", "--bbox", BOX, "--out", str(tmp_path), "--archive", str(tmp_path)]
    assert cli.main([*args, "--day", "10/10/2026"]) == 1
    assert "not a day" in capsys.readouterr().err

    def missing(*_: object) -> list[str]:
        raise ImportError("No module named 'matplotlib'")

    monkeypatch.setattr(r, "check_text", missing)
    (tmp_path / "a.md").write_text("1")
    (tmp_path / "a.json").write_text("{}")
    assert cli.main(["check", str(tmp_path / "a.md"), str(tmp_path / "a.json")]) == 1
    assert "pip install 'dpc-radar[report]'" in capsys.readouterr().err


@pytest.mark.parametrize("box", ["15,37,16", "a,b,c,d", "16,37,15,38", "15,38,16,37"])
def test_a_box_is_four_numbers_in_order(box: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(["report", "--bbox", box, "--day", "2026-10-10", "--out", "x"])
    assert stopped.value.code == 2 and "a box is W,S,E,N" in capsys.readouterr().err


def test_mcp(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("mcp")
    served: list[bool] = []
    monkeypatch.setattr("dpc_radar.mcp_server.run", lambda: served.append(True))
    assert cli.main(["mcp"]) == 0 and served == [True]

    def refused() -> None:
        raise ImportError("No module named 'mcp'")

    monkeypatch.setattr("dpc_radar.mcp_server.run", refused)
    assert cli.main(["mcp"]) == 1
    assert "pip install 'dpc-radar[mcp]'" in capsys.readouterr().err

    def broken() -> None:
        raise DpcRadarError("no")

    monkeypatch.setattr("dpc_radar.mcp_server.run", broken)
    assert cli.main(["mcp"]) == 1
