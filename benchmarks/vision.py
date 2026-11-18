"""An experiment: the cases of the benchmark as images, read by Claude.

The rules of the library see numbers: a jump between two frames, a value over
the record, rain the radar alone denies. They do not see *shapes*. This script
asks whether a model that looks at the map does. It draws every case as the
frame before, the frame and, where the case keeps it, the radar alone; sends
the picture to the Claude API; and scores the answer on the same checks as
every other method.

    uv run --group vision python -m benchmarks.vision render out/  # the pictures: no key
    uv run --group vision python -m benchmarks.vision ask          # call the API, save the answers
    uv run --group vision python -m benchmarks.vision score        # the table, from the answers

``ask`` needs ``ANTHROPIC_API_KEY`` (or any credential the Anthropic SDK
finds) and spends money: about 14 requests with one image each. The answers go
to ``vision-answers.json``, one set per model, so ``score`` is free, repeatable
and runs with no key. A case already answered is not asked again (``--again``).

**What the model gets.** The picture, the name of the product and how old the
frame before is. Not the title of the case, not its date, not a single number:
the scale stops at 500 mm, so a bell of 20,000 mm is a patch of the top colour.
The system prompt holds what the documentation says to anyone (how the
products are built, what goes wrong, that a round edge alone proves nothing).

**What it answers.** Whether the frame is usable, and the boxes, in degrees
read off the axes, of what should not be trusted. A rejected frame is "not
measured" everywhere; the pixels inside a box are "not measured". It does not
answer "can the radar be trusted here?": those two checks count as not passed,
as for every method that does not decide.

This is not a method of the library and it is not in ``METHODS``: it needs a
key, it costs, and the same picture can get two answers on two days.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.warp import transform as warp_points

from benchmarks.bench import HERE, Case, Frame, Result, load_cases, run
from benchmarks.run import MARK, summary
from dpc_radar import product

ANSWERS = HERE / "vision-answers.json"
MODEL = "claude-opus-5-5"

#: The edges of the colours, in mm. Above the last one everything looks the same.
LEVELS = (0.1, 1, 2, 5, 10, 20, 50, 100, 200, 500)
#: The longest side of a panel, in pixels of the grid: more is drawn one in ``step``.
MAX_SIDE = 700

KINDS = ("corrupted_frame", "faulty_gauge", "clutter", "radar_ring", "other")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "frame": {"type": "string", "enum": ["usable", "rejected"]},
        "regions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "west": {"type": "number"},
                    "south": {"type": "number"},
                    "east": {"type": "number"},
                    "north": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "west", "south", "east", "north", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "frame", "regions"],
    "additionalProperties": False,
}

SYSTEM = """\
You check rain maps of the Italian Civil Protection radar network (DPC) before \
a flood warning system reads them. For each picture you say what in the frame \
can be trusted.

The picture has two or three panels with the same area and the same axes \
(longitude and latitude, in degrees):
- "before": the frame issued one step earlier;
- "frame": the frame you judge;
- "radar alone", when it is there: the rain of the same hours from the \
radar-only product, with no rain gauge correction.

The products:
- CUM3 and CUM24 are the rain of the last 3 and 24 hours, in mm: the radar \
corrected with thousands of rain gauges. A new frame comes every 30 minutes, \
so two frames in a row share almost all of their hours and can differ only by \
half an hour of rain.
- SRT1 is the rain of the last hour, in mm, from the radar alone. A new frame \
comes every 5 minutes.

The colours are millimetres, on the scale at the side. The scale stops at 500: \
anything above has the top colour. White is no rain. Grey means no data: \
nobody measured there.

What goes wrong in these data:
- A corrupted frame carries rain that the frame before did not have and that \
no half hour can bring. It is wrong as a whole.
- A rain gauge gone wrong puts a smooth patch of heavy rain around itself in a \
CUM frame, because the gauges are spread over the map. It can be the same in \
the frame before. The radar alone shows no rain at all there.
- The radar alone often reads less than the gauges, and sometimes much less: \
beams blocked by mountains, rain that forms under the beam. Less rain on the \
radar is normal. Only no rain at all, where the CUM frame has a lot, is a sign.
- Echoes from the sea or the ground paint rain where there is none. They stay \
where they are from frame to frame.
- A round edge alone proves nothing: it is where the range of a radar ends, \
and real rain is cut there.

Two mistakes cost the same. Throwing away real rain hides a flood. Keeping a \
fault raises a false alarm. Most frames are fine: when nothing is wrong, say \
so.

Answer with:
- summary: what you see, in two or three sentences;
- frame: "rejected" if the frame cannot be used at all, "usable" otherwise;
- regions: for a usable frame, the boxes of what should not be trusted, as \
west, south, east and north in degrees read off the axes. Keep each box tight \
around the fault. An empty list when everything can be trusted, and for a \
rejected frame.\
"""


def lonlat(frame: Frame) -> tuple[np.ndarray, np.ndarray]:
    """The longitude and latitude of the centre of every pixel."""
    rows, cols = frame.data.shape
    t = frame.transform
    xs, ys = np.meshgrid(t.c + (np.arange(cols) + 0.5) * t.a, t.f + (np.arange(rows) + 0.5) * t.e)
    if frame.crs.is_geographic:
        return xs, ys
    lon, lat = warp_points(frame.crs, "EPSG:4326", xs.ravel(), ys.ravel())  # SRT1 is in metres
    return np.reshape(lon, xs.shape), np.reshape(lat, xs.shape)


def panels(case: Case) -> list[tuple[str, Frame]]:
    """The frames of a case, in the order they are drawn."""
    frame_path, previous_path = case.paths()
    minutes = int(product(case.product).period.total_seconds() // 60)
    found = [
        (
            f"before ({case.product}, {minutes} min earlier)",
            Frame.read(previous_path, case.product),
        ),
        (f"frame ({case.product})", Frame.read(frame_path, case.product)),
    ]
    if case.radar:
        found.append(("radar alone (same hours)", Frame.read(case.radar_path, "SRT1")))
    return found


def render(case: Case) -> bytes:
    """A case as a PNG: its panels side by side, one scale, axes in degrees."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap

    colours = plt.get_cmap("turbo")(np.linspace(0.08, 1.0, len(LEVELS)))
    cmap = ListedColormap(colours[:-1])
    cmap.set_under("white")
    cmap.set_over(colours[-1])
    cmap.set_bad("#b0b0b0")
    norm = BoundaryNorm(LEVELS, cmap.N)

    found = panels(case)
    west, south, east, north = case.bbox
    fig, axes = plt.subplots(
        1, len(found), figsize=(4.7 * len(found), 4.6), dpi=110, sharey=True, layout="constrained"
    )
    mesh = None
    for ax, (title, frame) in zip(np.atleast_1d(axes), found, strict=True):
        step = max(1, -(-max(frame.data.shape) // MAX_SIDE))
        lon, lat = lonlat(frame)
        mesh = ax.pcolormesh(
            lon[::step, ::step],
            lat[::step, ::step],
            np.ma.masked_invalid(frame.data[::step, ::step]),
            cmap=cmap,
            norm=norm,
            shading="nearest",
            rasterized=True,
        )
        ax.set_facecolor("#b0b0b0")
        ax.set_xlim(west, east)
        ax.set_ylim(south, north)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("longitude (°E)")
        ax.grid(color="black", alpha=0.25, linewidth=0.5)
        ax.minorticks_on()
    np.atleast_1d(axes)[0].set_ylabel("latitude (°N)")
    bar = fig.colorbar(mesh, ax=axes, extend="both", ticks=LEVELS, shrink=0.9)
    bar.set_label("mm (grey: no data)")
    out = io.BytesIO()
    fig.savefig(out, format="png")
    plt.close(fig)
    return out.getvalue()


def ask(client: Any, case: Case, model: str) -> dict[str, Any]:
    """What the model says of a case: its answer, or why there is none."""
    names = ", ".join(title for title, _ in panels(case))
    answer = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        # A request Claude Opus 5.5 declines is run again on the model the API
        # chooses, in the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.standard_b64encode(render(case)).decode(),
                        },
                    },
                    {"type": "text", "text": f"Product: {case.product}. Panels: {names}."},
                ],
            }
        ],
    )
    usage = {"input": answer.usage.input_tokens, "output": answer.usage.output_tokens}
    if answer.stop_reason != "end_turn":
        return {"error": f"stopped: {answer.stop_reason}", "usage": usage, "model": answer.model}
    text = next(block.text for block in answer.content if block.type == "text")
    return {**json.loads(text), "usage": usage, "model": answer.model}


@dataclass(frozen=True)
class Recorded:
    """A saved answer, as a method of the benchmark for the one case it is about."""

    answer: dict[str, Any]
    name: str = "vision"
    description: str = "Claude, looking at the picture"

    def frame_ok(self, frame: Frame, previous: Frame, radar: Frame | None) -> bool | None:
        return None if "frame" not in self.answer else self.answer["frame"] == "usable"

    def clean(self, frame: Frame, previous: Frame, radar: Frame | None) -> np.ndarray:
        if self.answer.get("frame") == "rejected":
            return np.full_like(frame.data, np.nan)  # rejected: nothing measured
        out = frame.data.copy()
        lon, lat = lonlat(frame)
        for r in self.answer.get("regions", []):
            inside = (
                (lon >= r["west"]) & (lon <= r["east"]) & (lat >= r["south"]) & (lat <= r["north"])
            )
            out[inside] = np.nan
        return out

    def reliable(self, lon: float, lat: float) -> bool | None:
        return None


def load(path: Path = ANSWERS) -> dict[str, dict[str, dict[str, Any]]]:
    return json.loads(path.read_text()) if path.exists() else {}


def results(answers: dict[str, dict[str, Any]], cases: list[Case]) -> list[Result]:
    """The checks of the cases that have an answer, scored like any method."""
    found: list[Result] = []
    for case in cases:
        if case.id in answers:
            found += run(Recorded(answers[case.id]), [case])
    return found


def text(model: str, answers: dict[str, dict[str, Any]], cases: list[Case]) -> str:
    scored = results(answers, cases)
    lines = [f"\nvision · {model}"]
    case = None
    for r in scored:
        if r.case is not case:
            case = r.case
            answer = answers[case.id]
            said = answer.get("error") or answer["frame"]
            boxes = "".join(f" [{b['kind']}]" for b in answer.get("regions", []))
            lines.append(f"  {case.id} [{case.status}] · {said}{boxes}")
            if "summary" in answer:
                lines.append(f"      {answer['summary']}")
        lines.append(f"    {MARK[r.outcome]} {r.check.label():<62} got {r.got}")
    missing = [c.id for c in cases if c.id not in answers]
    if missing:
        lines.append(f"  not asked: {', '.join(missing)}")
    tokens = [a.get("usage", {}) for a in answers.values()]
    lines.append(
        f"  score · {summary(scored)}"
        f" · {sum(t.get('input', 0) for t in tokens)} tokens in,"
        f" {sum(t.get('output', 0) for t in tokens)} out"
    )
    return "\n".join(lines)


def _render(args: argparse.Namespace, cases: list[Case]) -> int:
    args.folder.mkdir(parents=True, exist_ok=True)
    for case in cases:
        path = args.folder / f"{case.id}.png"
        path.write_bytes(render(case))
        print(path)
    return 0


def _ask(args: argparse.Namespace, cases: list[Case]) -> int:
    import anthropic

    saved = load()
    answers = saved.setdefault(args.model, {})
    try:
        client = anthropic.Anthropic()
        for case in cases:
            if case.id in answers and "error" not in answers[case.id] and not args.again:
                continue
            answers[case.id] = ask(client, case, args.model)
            ANSWERS.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
            print(f"{case.id}: {answers[case.id].get('error') or answers[case.id]['frame']}")
    except TypeError as error:  # what the SDK raises when it finds no credential
        if "authentication" not in str(error):
            raise
        print("No key: set ANTHROPIC_API_KEY, then run `ask` again.", file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print("The key was refused: check ANTHROPIC_API_KEY.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as error:
        print(f"The API answered {error.status_code}: {error.message}", file=sys.stderr)
        return 1
    except anthropic.AnthropicError as error:  # no key at all, or no network
        print(f"No answer from the API: {error}", file=sys.stderr)
        return 1
    print(text(args.model, answers, cases))
    return 0


def _score(args: argparse.Namespace, cases: list[Case]) -> int:
    saved = load()
    models = [args.model] if args.model else list(saved)
    if not models or any(m not in saved for m in models):
        print("No saved answers: run `python -m benchmarks.vision ask` first.", file=sys.stderr)
        return 1
    for model in models:
        print(text(model, saved[model], cases))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.vision")
    commands = parser.add_subparsers(dest="command", required=True)
    draw = commands.add_parser("render", help="write the picture of every case (no key needed)")
    draw.add_argument("folder", type=Path)
    draw.set_defaults(run=_render)
    call = commands.add_parser(
        "ask", help="send the pictures to the Claude API and save the answers"
    )
    call.add_argument("--model", default=MODEL)
    call.add_argument("--again", action="store_true", help="ask again the cases already answered")
    call.set_defaults(run=_ask)
    table = commands.add_parser("score", help="score the saved answers")
    table.add_argument("--model", help="one model (default: every model with answers)")
    table.set_defaults(run=_score)
    for command in (draw, call, table):
        command.add_argument("cases", nargs="*", help="case ids (default: all)")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.cases:
        unknown = sorted(set(args.cases) - {c.id for c in cases})
        if unknown:
            parser.error(f"unknown case(s): {', '.join(unknown)}")
        cases = [c for c in cases if c.id in args.cases]
    return int(args.run(args, cases))


if __name__ == "__main__":
    sys.exit(main())
