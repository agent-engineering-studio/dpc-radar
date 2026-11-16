"""Baseline methods: what Limen did before and does today.

They are here to be beaten. A new method goes in this file (or anywhere,
registered in ``METHODS``) and is run with ``python -m benchmarks.run <name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from benchmarks.bench import Frame, Method
from dpc_radar import FrameCheck, Quality, RainGrid, product, reliability


@dataclass(frozen=True)
class AcceptAll:
    """Trust every pixel of every frame."""

    name: str = "accept-all"
    description: str = "trust everything (no cleaning)"

    def frame_ok(self, frame: Frame, previous: Frame, radar: Frame | None) -> bool | None:
        return True

    def clean(self, frame: Frame, previous: Frame, radar: Frame | None) -> np.ndarray:
        return frame.data

    def reliable(self, lon: float, lat: float) -> bool | None:
        return None


@dataclass(frozen=True)
class LimenMaxStep:
    """Limen since #235 (October 2026): a pixel that moved more than
    ``max_step`` mm since the frame before is "not measured". No decision on
    the whole frame, no notion of where the radar is blind. Points are read with
    the 3x3 median, as everywhere in this benchmark.
    """

    max_step: float = 150.0
    name: str = "limen-max-step"
    description: str = "Limen today: mask pixels that jump > 150 mm in 30 min (limen#235)"

    def frame_ok(self, frame: Frame, previous: Frame, radar: Frame | None) -> bool | None:
        return None

    def clean(self, frame: Frame, previous: Frame, radar: Frame | None) -> np.ndarray:
        a, b = frame.data, previous.data
        bad = np.isfinite(a) & np.isfinite(b) & (np.abs(a - b) > self.max_step)
        out = a.copy()
        out[bad] = np.nan
        return out

    def reliable(self, lon: float, lat: float) -> bool | None:
        return None


@dataclass(frozen=True)
class JumpSign:
    """A decision on the whole frame, from the sign of the jump (#6, first step).

    This is :meth:`dpc_radar.RainGrid.check`, the library's own rule, run on the
    benchmark: two CUM24 frames 30 minutes apart can differ by at most half an
    hour of rain; where they differ by more than ``max_step`` on at least
    ``min_pixels`` pixels, the frame that jumps *up* is rejected whole and the
    one that jumps *down* (the clean frame after a corrupted one) is kept
    intact. Fewer jumps: just those pixels are masked.

    Limits: it needs the frame before; it cannot see artefacts that do not
    move between frames (a stuck rain gauge); in this benchmark it
    decides on the case's area, not on the whole of Italy.
    """

    max_step: float = 150.0
    min_pixels: int = 5
    name: str = "jump-sign"
    description: str = "RainGrid.check: reject the frame that jumps up > 150 mm (#6)"

    def _check(self, frame: Frame, previous: Frame) -> FrameCheck:
        now, before = _grid(frame), _grid(previous)
        return now.check(before, max_step=self.max_step, min_pixels=self.min_pixels)

    def frame_ok(self, frame: Frame, previous: Frame, radar: Frame | None) -> bool | None:
        return self._check(frame, previous).usable

    def clean(self, frame: Frame, previous: Frame, radar: Frame | None) -> np.ndarray:
        result = self._check(frame, previous)
        if result.grid is None:
            return np.full_like(frame.data, np.nan)  # rejected: nothing measured
        return result.grid.data

    def reliable(self, lon: float, lat: float) -> bool | None:
        return None


@dataclass(frozen=True)
class JumpSignMap(JumpSign):
    """:class:`JumpSign` on the frames, and the reliability map (#3) on the
    question "can the radar be trusted here?".

    The map is static: it knows where the beam passes high, where a radar is
    often off and where the radar alone reads far from the «Merging». It does
    not know that a radar was off on that very day.
    """

    name: str = "jump-sign-map"
    description: str = "jump-sign plus dpc_radar.reliability, the static map (#3)"

    def reliable(self, lon: float, lat: float) -> bool | None:
        return reliability(lon, lat).reliable


@dataclass(frozen=True)
class QualityCheck(JumpSignMap):
    """The library today: :meth:`dpc_radar.RainGrid.quality` on the frames (#6)
    and the reliability map on the places.

    On top of the jump rule: values beyond the Italian record, pixels far above
    their neighbours and, where the case keeps the radar-only rain of the same
    window, «Merging» rain the radar did not see where it sees well (a rain
    gauge gone wrong).
    """

    name: str = "quality"
    description: str = "RainGrid.quality: jump, record, outlier, gauge the radar denies (#6)"

    def _quality(self, frame: Frame, previous: Frame, radar: Frame | None) -> Quality:
        return _grid(frame).quality(
            _grid(previous),
            None if radar is None else _grid(radar),
            max_step=self.max_step,
            min_pixels=self.min_pixels,
        )

    def frame_ok(self, frame: Frame, previous: Frame, radar: Frame | None) -> bool | None:
        return self._quality(frame, previous, radar).usable

    def clean(self, frame: Frame, previous: Frame, radar: Frame | None) -> np.ndarray:
        result = self._quality(frame, previous, radar)
        if result.grid is None:
            return np.full_like(frame.data, np.nan)
        return result.grid.data


def _grid(frame: Frame) -> RainGrid:
    """A benchmark frame as the library's grid (the time does not matter here)."""
    return RainGrid(
        product(frame.product),
        datetime.min.replace(tzinfo=UTC),
        frame.data.astype("float32"),
        frame.transform,
        frame.crs,
    )


METHODS: dict[str, Method] = {
    m.name: m for m in (AcceptAll(), LimenMaxStep(), JumpSign(), JumpSignMap(), QualityCheck())
}
