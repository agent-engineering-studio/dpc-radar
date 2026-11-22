"""How faithfully a text quotes the facts: numbers invented, swapped, rounded the wrong way.

The tools of the MCP server answer with facts (:class:`dpc_radar.facts.Facts`);
a model turns them into words. :func:`dpc_radar.report.check_text` refuses a
number that is not in the facts, :func:`dpc_radar.report.check_claims` one said
of the wrong fact in the sentences of the report template. A model writes its
own sentences: this set measures what it does with the numbers.

    uv run python -m benchmarks.faithful score            # the table: no key, no network
    uv run python -m benchmarks.faithful score --check    # the hand answers score as written
    uv run --group vision python -m benchmarks.faithful ask     # a model answers (API key)
    uv run --group vision python -m benchmarks.faithful label   # a second model reads them
    uv run python -m benchmarks.faithful facts --archive <folder of archive()>

**The set** (``faithful.json``). Each case is a question, the tool and the
arguments that answer it, and the facts the tool gave, recorded once from a
local archive (``facts``): the night of Melito, 10 October 2026. Then the
answers: a text, and for every number in it, in order, the fact it is said of
(``said``: a path into the facts, ``peak.wettest.1.mm``, or ``null`` for a
number of the question). Two answers per case are written by hand, one
faithful and one with known faults; their ``expect`` is what ``score`` must
find, and ``score --check`` fails if it does not. That is the test of the
scorer, not of a model.

**A model's answers.** ``ask`` sends each question with its facts to a model
and saves the text; ``label`` asks a second model what each number of a text is
said of, and saves its ``said``. The paths of a model are an opinion: the hand
answers are labelled by both, and ``score`` prints how often the reader's labels
give a number the same score as the hand's. Both commands need
``ANTHROPIC_API_KEY`` and spend money (one request per case and answer);
``score`` is free and repeatable.

**The score** of a number, given what it is said of:

* ``faithful``: the fact, to the decimals written (``118`` and ``118.0`` for 118.04),
  or the key it is said of (20 in "205.1 km² got 20 mm or more");
* ``approximate``: a round number, the fact rounded to its zeros (``3,000``
  for 3002): not a fault;
* ``question``: said of nothing in the facts, and written in the question;
* ``rounded``: not the fact, but within one unit of its last digit (``118.1``
  for 118.04, ``191`` for 191.84): a wrong rounding or a cut;
* ``swapped``: not the fact, but another figure of the same facts;
* ``invented``: none of these.

Limen's reviewer (``fedelta``) is the model: a deterministic check first, then
a second model for what a regex does not see. Here the second model only says
what a number is about; whether it is right is arithmetic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dpc_radar.report import check_text
from dpc_radar.report import numbers as numbers_of

HERE = Path(__file__).parent
SET = HERE / "faithful.json"
MODEL = "claude-opus-5-5"
READER = "claude-sonnet-5-5"
KINDS = ("faithful", "approximate", "question", "rounded", "swapped", "invented")
#: The kinds that are faults.
FAULTS = ("rounded", "swapped", "invented")

_DIGITS = re.compile(r"\d+(?:\.\d+)?")

WRITER = (
    "You answer questions about rain in Italy for people who are not meteorologists. "
    "You get the question and the facts a tool returned, as JSON. Answer in a few "
    "sentences of plain English, using only the facts: no number that is not in them, "
    "and say what each number is. Times in the facts are UTC."
)
LABELLER = (
    "You read a short text written from a JSON of facts. For every number in the text, "
    "in the order they appear, say which fact the sentence says it is: a path into the "
    "JSON with dots, list positions counted from 0 (peak.wettest.1.mm, frame.time). A "
    "time like 03:00 is two numbers, both of the same time fact. If the sentence says "
    "the number is something the JSON does not hold, give the path of the fact it names "
    "anyway if there is one; if the number only repeats the question, give null. Do not "
    "judge whether the number is right."
)
LABELS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "said": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "of": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["number", "of"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["said"],
    "additionalProperties": False,
}


def numbers(text: str) -> list[str]:
    """The numbers of a text as written (:func:`dpc_radar.report.numbers`), bold or not."""
    return numbers_of(text.replace("**", ""))


def at(facts: Any, path: str) -> Any:
    """The fact at a dotted path (list positions as numbers); ``None`` when there is none."""
    for part in path.split("."):
        if isinstance(facts, dict) and part in facts:
            facts = facts[part]
        elif isinstance(facts, list) and part.isdigit() and int(part) < len(facts):
            facts = facts[int(part)]
        else:
            return None
    return facts


def values(fact: Any) -> Iterator[float]:
    """The numbers a fact holds: itself, or the digits of a string (a time, a date)."""
    if isinstance(fact, bool) or fact is None:
        return
    if isinstance(fact, int | float):
        yield abs(float(fact))
    elif isinstance(fact, str):
        yield from (float(d) for d in _DIGITS.findall(fact))
    elif isinstance(fact, dict):  # a key can be a figure too: km2_over {"20": 205.1}
        for key, item in fact.items():
            yield from values(key)
            yield from values(item)
    elif isinstance(fact, list):
        for item in fact:
            yield from values(item)


def same(token: str, value: float) -> bool:
    decimals = len(token.partition(".")[2])
    return round(value, decimals) == float(token.replace(",", ""))


def near(token: str, value: float) -> bool:
    """Within one unit of the last digit written, and not the same."""
    decimals = len(token.partition(".")[2])
    return abs(value - float(token.replace(",", ""))) < 10**-decimals and not same(token, value)


def round_number(token: str, value: float) -> bool:
    """``3,000`` for 3002, ``600`` for 646: a round number, the fact rounded to its zeros."""
    digits = token.replace(",", "")
    zeros = len(digits) - len(digits.rstrip("0"))
    if "." in digits or not zeros or zeros == len(digits):
        return False
    return round(value, -zeros) == float(digits)


def is_key(token: str, of: str, facts: dict[str, Any]) -> bool:
    """The number is the key the path ends with: 20 in ``km2_over.20`` ("20 mm or more")."""
    parent, _, last = of.rpartition(".")
    held = at(facts, parent) if parent else facts
    if not isinstance(held, dict) or last not in held or not _DIGITS.fullmatch(last):
        return False
    return float(last) == float(token.replace(",", ""))


def kind(token: str, of: str | None, facts: dict[str, Any], question: str) -> str:
    """What a number is, given the fact it is said of (see the module)."""
    everything = list(values(facts))
    if of is None:
        if token in numbers(question):
            return "question"
        return "swapped" if any(same(token, v) for v in everything) else "invented"
    own = list(values(at(facts, of)))
    if any(same(token, v) for v in own) or is_key(token, of, facts):
        return "faithful"
    if any(round_number(token, v) for v in own):
        return "approximate"
    if any(near(token, v) for v in own):
        return "rounded"
    if any(same(token, v) for v in everything):
        return "swapped"
    return "invented"


def score(case: dict[str, Any], answer: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    """Every number of an answer: as written, what it is said of, and its kind.

    :class:`ValueError` if the labels do not follow the numbers of the text.
    """
    found, said = numbers(answer["text"]), answer.get("said")
    if said is None:
        raise ValueError("not labelled: run `label` first")
    if [s["number"] for s in said] != found:
        raise ValueError(f"the labels {[s['number'] for s in said]} are not the numbers {found}")
    return [
        (s["number"], s["of"], kind(s["number"], s["of"], case["facts"], case["question"]))
        for s in said
    ]


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SET.read_text())
    return data


def save(data: dict[str, Any]) -> None:
    SET.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def table(data: dict[str, Any]) -> tuple[str, list[str]]:
    """The table by author, and what goes against the ``expect`` of a hand answer."""
    totals: dict[str, Counter[str]] = {}
    faults: list[str] = []
    details: list[str] = []
    for case in data["cases"]:
        for answer in case["answers"]:
            try:
                scored = score(case, answer)
            except ValueError as error:
                details.append(f"- {case['id']}, {answer['by']}: {error}")
                continue
            got = Counter(k for _, _, k in scored)
            totals.setdefault(answer["by"], Counter()).update(got)
            totals[answer["by"]]["answers"] += 1
            if any(got[k] for k in FAULTS):
                strangers = set(check_text(answer["text"], case["facts"]))
                totals[answer["by"]]["missed"] += sum(
                    k in FAULTS and n not in strangers for n, _, k in scored
                )
                wrong = [f"{n} ({k}, said of {of})" for n, of, k in scored if k in FAULTS]
                details.append(f"- {case['id']}, {answer['by']}: " + "; ".join(wrong))
            expect = answer.get("expect")
            if expect is not None and {k: got[k] for k in KINDS if got[k]} != expect:
                faults.append(f"{case['id']}, {answer['by']}: expected {expect}, got {dict(got)}")
    head = "| Author | Answers | Numbers | " + " | ".join(KINDS) + " | faults check_text misses |"
    lines = [head, "|---" * (4 + len(KINDS)) + "|"]
    for by, got in totals.items():
        count = sum(got[k] for k in KINDS)
        cells = [str(got[k]) for k in KINDS]
        faults_ = sum(got[k] for k in FAULTS)
        missed = f"{got['missed']} of {faults_}" if faults_ else "-"
        lines.append(
            f"| {by} | {got['answers']} | {count} | " + " | ".join(cells) + f" | {missed} |"
        )
    agreement = _reader_agreement(data)
    if agreement:
        lines += ["", agreement]
    if details:
        lines += ["", "What is not faithful:", *details]
    return "\n".join(lines), faults


def _reader_agreement(data: dict[str, Any]) -> str:
    """How often the reading of a model gives a hand-labelled number the same score."""
    same_kind = same_verdict = total = 0
    reader = ""
    for case in data["cases"]:
        for answer in case["answers"]:
            hand = answer.get("said") or []
            for model, said in (answer.get("read_by") or {}).items():
                if [s["number"] for s in said] != [s["number"] for s in hand]:
                    continue
                reader = model
                for a, b in zip(said, hand, strict=True):
                    ka = kind(a["number"], a["of"], case["facts"], case["question"])
                    kb = kind(b["number"], b["of"], case["facts"], case["question"])
                    total += 1
                    same_kind += ka == kb
                    same_verdict += (ka in FAULTS) == (kb in FAULTS)
    if not total:
        return ""
    return (
        f"Read by {reader}, the hand answers score the same on {same_kind} of {total} "
        f"numbers, and the verdict (a fault or not) is the same on {same_verdict}."
    )


# The facts, recorded once from a local archive.


class _Archive:
    """A :class:`dpc_radar.facts.Source` that serves an archive as if it were ``now``."""

    def __init__(self, folder: Path, now: datetime) -> None:
        from dpc_radar.archive import MergingArchive

        self._archive = MergingArchive(folder)
        self._now = now

    async def last_product(self, name: Any) -> Any:
        from dpc_radar.products import LastProduct, product

        p = product(name)
        period = timedelta(hours=1) if p.name == "SRT1" else timedelta(minutes=30)
        return LastProduct(p, self._now, period)

    async def at(self, name: Any, time: datetime) -> Any:
        from dpc_radar.client import Absent
        from dpc_radar.products import product

        grid = self._archive.read(product(name).name, time)
        return Absent() if grid is None else grid


async def _record(case: dict[str, Any], folder: Path) -> dict[str, Any]:
    from dpc_radar.facts import Facts

    now = datetime.fromisoformat(case["now"]).replace(tzinfo=UTC)
    facts = Facts(_Archive(folder, now))
    method = getattr(facts, case["tool"])
    found = method(**case["args"])
    return await found if asyncio.iscoroutine(found) else found  # type: ignore[no-any-return]


def _report(case: dict[str, Any], folder: Path) -> dict[str, Any]:
    from datetime import date

    from dpc_radar.archive import MergingArchive
    from dpc_radar.report import build

    args = case["args"]
    return build(
        MergingArchive(folder).read, tuple(args["bbox"]), date.fromisoformat(args["day"])
    ).facts


def _facts(args: argparse.Namespace, data: dict[str, Any]) -> int:
    for case in data["cases"]:
        if case["tool"] == "report":
            case["facts"] = _report(case, args.archive)
        else:
            case["facts"] = asyncio.run(_record(case, args.archive))
        print(f"{case['id']}: {len(json.dumps(case['facts']))} characters of facts")
    save(data)
    return 0


# The model: one writes, another reads.


def _client() -> Any:
    import anthropic

    return anthropic.Anthropic()


def _ask(args: argparse.Namespace, data: dict[str, Any]) -> int:
    client = _client()
    for case in data["cases"]:
        if any(a["by"] == args.model for a in case["answers"]) and not args.again:
            continue
        message = client.messages.create(
            model=args.model,
            max_tokens=2000,
            system=WRITER,
            messages=[
                {
                    "role": "user",
                    "content": f"QUESTION: {case['question']}\n\nFACTS:\n"
                    + json.dumps(case["facts"], ensure_ascii=False),
                }
            ],
        )
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        case["answers"] = [a for a in case["answers"] if a["by"] != args.model]
        case["answers"].append({"by": args.model, "text": text})
        save(data)
        print(f"{case['id']}: {len(numbers(text))} numbers")
    return 0


def _read(client: Any, model: str, case: dict[str, Any], text: str) -> list[dict[str, Any]]:
    message = client.messages.create(
        model=model,
        max_tokens=4000,
        system=LABELLER,
        messages=[
            {
                "role": "user",
                "content": f"QUESTION: {case['question']}\n\nFACTS:\n"
                + json.dumps(case["facts"], ensure_ascii=False)
                + f"\n\nTEXT:\n{text}",
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": LABELS}},
    )
    raw = next(b.text for b in message.content if b.type == "text")
    said: list[dict[str, Any]] = json.loads(raw)["said"]
    return said


def _label(args: argparse.Namespace, data: dict[str, Any]) -> int:
    client = _client()
    for case in data["cases"]:
        for answer in case["answers"]:
            said = None
            labels = [x["number"] for x in answer.get("said") or []]
            if answer.get("expect") is None and labels != numbers(answer["text"]):
                said = answer["said"] = _read(client, args.model, case, answer["text"])
                answer["labelled_by"] = args.model
            elif answer.get("expect") is not None and args.model not in answer.get("read_by", {}):
                said = _read(client, args.model, case, answer["text"])
                answer.setdefault("read_by", {})[args.model] = said
            if said is not None:
                save(data)
                print(f"{case['id']}, {answer['by']}: {len(said)} numbers read")
    return 0


def _score(args: argparse.Namespace, data: dict[str, Any]) -> int:
    text, faults = table(data)
    print(text)
    if args.check and faults:
        print("\nThe hand answers do not score as expected:", *faults, sep="\n", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.faithful")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("facts", help="record the facts of every case from an archive")
    record.add_argument("--archive", type=Path, required=True)
    record.set_defaults(run=_facts)
    write = commands.add_parser("ask", help="a model answers every question (API key)")
    write.add_argument("--model", default=MODEL)
    write.add_argument("--again", action="store_true", help="replace the answers of that model")
    write.set_defaults(run=_ask)
    read = commands.add_parser("label", help="a second model says what each number is of")
    read.add_argument("--model", default=READER)
    read.set_defaults(run=_label)
    scored = commands.add_parser("score", help="the table, from the saved answers")
    scored.add_argument("--check", action="store_true", help="fail if a hand answer scores wrong")
    scored.set_defaults(run=_score)
    args = parser.parse_args(argv)
    return int(args.run(args, load()))


if __name__ == "__main__":
    sys.exit(main())
