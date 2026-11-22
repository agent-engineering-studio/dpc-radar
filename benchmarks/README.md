# Benchmark: known cases of the DPC radar data

A small, fixed set of real frames where we already know the right answer.
Every method that decides what to trust in a frame (a cleaning rule, a learned
quality check, a reliability map) runs here before it reaches
[Limen](https://github.com/agent-engineering-studio/limen). Without it, every
improvement is an opinion. Tracked in
[#2](https://github.com/agent-engineering-studio/dpc-radar/issues/2).

## The cases

| Case | What happened | What a method must do |
|---|---|---|
| **Melito, 10 October 2026** | the Reggio Calabria storm: 175–185 mm in 24 h, forecast 20 mm | keep it: it is real |
| **Lipari, same day** | a cloudburst the radar did not see | say the radar is not reliable there |
| **The corrupted :30 series** | a smooth bell of up to 20,584 mm over Abruzzo in every CUM24 frame at :30, on 30/09–02/10, 08–11/10 and 21/10–03/11 ([limen#235](https://github.com/agent-engineering-studio/limen/issues/235)) | reject the frame, or at least every wrong pixel |
| **The clean frame after it** | the :00 frame is right, but differs from the corrupted one before it | keep its pixels |
| **Calm days, 4 and 6 October** | nothing | change nothing |
| **A stuck rain gauge, 6–8 October** | 126.6 mm in every 3-hour frame for 33 hours on the Lazio–Abruzzo border (1,152 mm in CUM24), on a night the radar alone reads 0 | remove it: it is not rain |
| **A gauge that fires at random, 29 September–26 October** | bursts of up to 130 mm in 3 hours north of Cassino, with a dry radar | remove it |
| **A gauge stuck at 7 mm, 2–5 October** | 7.18 mm in 27 three-hour frames in a row east of L'Aquila, radar 0 | remove it |
| **A gauge that always rains, 29 September–6 October** | 1 to 13 mm in 58 three-hour frames in a row in Irpinia, radar 0 | remove it |
| **The same night, radar only (SRT1)** | nothing | change nothing |
| **Melito, radar only (SRT1)** | one hour of the storm: 21 mm at Melito | keep it |
| **Rain west of Sardinia, 20 October** | 59 mm at sea in round fields that do not move: it looked like sea clutter, it is a band of rain | keep it |
| **The same band, radar only (SRT1)** | 21 mm in the hour it passes | keep it |

Each case is one frame and the one before it (30 minutes for the «Merging»,
5 for the radar-only `SRT1`), cropped to its area
and committed in [`frames/`](frames/) (CC BY-SA 4.0, © Dipartimento della
Protezione Civile). The DPC deletes frames after about five months: once
committed, a case keeps working.

## Today's scores

`limen-max-step` is what Limen does today: it throws away a pixel that moved
more than 150 mm in 30 minutes. `accept-all` trusts everything. `jump-sign` is
the first step of the frame quality check
([#6](https://github.com/agent-engineering-studio/dpc-radar/issues/6)): two
frames 30 minutes apart can differ by at most half an hour of rain, so where
they differ by more one of them is wrong, and the **sign** says which. The
corruption always adds rain: the frame that jumps *up* is rejected whole, the
one that jumps *down* (the clean frame after a corrupted one) is kept intact.
`jump-sign-map` adds the reliability map
([#3](https://github.com/agent-engineering-studio/dpc-radar/issues/3)) to
answer "can the radar be trusted here?". `quality` is what the library does
today (`RainGrid.quality`): the same, plus three
rules that need no frame before. The one that counts here compares the
«Merging» with the radar alone over the same hours: rain that the radar did
not see at all, where the radar sees well, is a rain gauge gone wrong.

| Case | Check | `accept-all` | `limen-max-step` | `jump-sign` | `jump-sign-map` | `quality` |
|---|---|---|---|---|---|---|
| `melito-2026-10-10T06` | frame usable | ✅ usable | — no frame decision | ✅ usable | ✅ usable | ✅ usable |
| `melito-2026-10-10T06` | Melito di Porto Salvo: 100-300 mm | ✅ 174.7 mm | ✅ 174.7 mm | ✅ 174.7 mm | ✅ 174.7 mm | ✅ 174.7 mm |
| `melito-2026-10-10T06` | Bagaladi: 80-250 mm | ✅ 113.1 mm | ✅ 113.1 mm | ✅ 113.1 mm | ✅ 113.1 mm | ✅ 113.1 mm |
| `melito-2026-10-10T06` | Melito di Porto Salvo: the radar saw the storm: reliable | — no reliability | — no reliability | — no reliability | ✅ reliable | ✅ reliable |
| `melito-2026-10-10T06` | Lipari: a cloudburst the radar did not see: not reliable | — no reliability | — no reliability | — no reliability | ✅ not reliable | ✅ not reliable |
| `melito-2026-10-11T00` | Melito di Porto Salvo: 150-300 mm | ✅ 184.9 mm | ✅ 184.9 mm | ✅ 184.9 mm | ✅ 184.9 mm | ✅ 184.9 mm |
| `bell-2026-10-11T0930` | frame rejected | ❌ usable | — no frame decision | ✅ rejected | ✅ rejected | ✅ rejected |
| `bell-2026-10-11T0930` | the bell's peak (7,348 mm): 0-100 mm or not measured | ❌ 6180.7 mm | ✅ not measured | ✅ not measured | ✅ not measured | ✅ not measured |
| `bell-2026-10-11T0930` | the bell's edge: 88 mm where the :00 frame reads 9: 0-30 mm or not measured | ❌ 87.7 mm | ❌ 87.7 mm | ✅ not measured | ✅ not measured | ✅ not measured |
| `after-bell-2026-10-11T10` | frame usable | ✅ usable | — no frame decision | ✅ usable | ✅ usable | ✅ usable |
| `after-bell-2026-10-11T10` | Gran Sasso, the point of limen#235 (7.3 mm in the :00 series): 2-30 mm | ✅ 7.3 mm | ❌ not measured | ✅ 7.3 mm | ✅ 7.3 mm | ✅ 7.3 mm |
| `bell-2026-10-09T0630` | frame rejected | ❌ usable | — no frame decision | ✅ rejected | ✅ rejected | ✅ rejected |
| `bell-2026-10-09T0630` | the bell's peak (13,226 mm): 0-100 mm or not measured | ❌ 10184.8 mm | ✅ not measured | ✅ not measured | ✅ not measured | ✅ not measured |
| `bell-2026-10-25T0630` | frame rejected | ❌ usable | — no frame decision | ✅ rejected | ✅ rejected | ✅ rejected |
| `bell-2026-10-25T0630` | the bell's peak (10,861 mm): 0-100 mm or not measured | ❌ 6510.3 mm | ✅ not measured | ✅ not measured | ✅ not measured | ✅ not measured |
| `calm-2026-10-04T06` | frame usable | ✅ usable | — no frame decision | ✅ usable | ✅ usable | ✅ usable |
| `calm-2026-10-04T06` | at most 0.1% of pixels masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.02% masked |
| `calm-2026-10-06T06` | frame usable | ✅ usable | — no frame decision | ✅ usable | ✅ usable | ✅ usable |
| `calm-2026-10-06T06` | at most 0.1% of pixels masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.01% masked |
| `steady-bump-2026-10-07T12` | the bump (1,152 mm): 0-300 mm or not measured | ❌ 999.9 mm | ❌ 999.9 mm | ❌ 999.9 mm | ❌ 999.9 mm | ✅ not measured |
| `stuck-gauge-cum3-2026-10-07T06` | the stuck gauge (126.6 mm in 3 h, radar 0): 0-20 mm or not measured | ❌ 126.6 mm | ❌ 126.6 mm | ❌ 126.6 mm | ❌ 126.6 mm | ✅ not measured |
| `burst-gauge-cum3-2026-10-02T06` | the gauge (129.9 mm in 3 h, radar 0): 0-20 mm or not measured | ❌ 129.9 mm | ❌ 129.9 mm | ❌ 129.9 mm | ❌ 129.9 mm | ✅ not measured |
| `stuck-low-cum3-2026-10-04T06` | the gauge (7.2 mm in 3 h, 27 times in a row, radar 0): 0-1 mm or not measured | ❌ 7.2 mm | ❌ 7.2 mm | ❌ 7.2 mm | ❌ 7.2 mm | ❌ 7.2 mm |
| `drizzle-gauge-cum3-2026-10-03T06` | the gauge (12.6 mm in 3 h, radar 0): 0-1 mm or not measured | ❌ 12.6 mm | ❌ 12.6 mm | ❌ 12.6 mm | ❌ 12.6 mm | ❌ 12.6 mm |
| `stuck-gauge-srt1-2026-10-07T06` | frame usable | ✅ usable | — no frame decision | ✅ usable | ✅ usable | ✅ usable |
| `stuck-gauge-srt1-2026-10-07T06` | over the stuck gauge: dry: 0-1 mm | ✅ 0.0 mm | ✅ 0.0 mm | ✅ 0.0 mm | ✅ 0.0 mm | ✅ 0.0 mm |
| `stuck-gauge-srt1-2026-10-07T06` | at most 0.1% of pixels masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.00% masked | ✅ 0.00% masked |
| `melito-srt1-2026-10-10T02` | frame usable | ✅ usable | — no frame decision | ✅ usable | ✅ usable | ✅ usable |
| `melito-srt1-2026-10-10T02` | Melito di Porto Salvo, one hour: 10-100 mm | ✅ 21.2 mm | ✅ 21.2 mm | ✅ 21.2 mm | ✅ 21.2 mm | ✅ 21.2 mm |
| `melito-srt1-2026-10-10T02` | Bagaladi, one hour: 10-100 mm | ✅ 24.5 mm | ✅ 24.5 mm | ✅ 24.5 mm | ✅ 24.5 mm | ✅ 24.5 mm |
| `sardinia-disks-2026-10-21T00` | at sea, inside the 'disks' (59 mm): 20-120 mm | ✅ 56.2 mm | ✅ 56.2 mm | ✅ 56.2 mm | ✅ 56.2 mm | ✅ 56.2 mm |
| `sardinia-band-srt1-2026-10-20T13` | frame usable | ✅ usable | — no frame decision | ✅ usable | ✅ usable | ✅ usable |
| `sardinia-band-srt1-2026-10-20T13` | at sea, under the band: 10-60 mm | ✅ 21.5 mm | ✅ 21.5 mm | ✅ 21.5 mm | ✅ 21.5 mm | ✅ 21.5 mm |
| **Score** | confirmed · suspect | confirmed: 19/33 | confirmed: 14/33 | confirmed: 26/33 | confirmed: 28/33 | confirmed: 31/33 |

"—" means the method does not decide; it counts as not passed.

### What the table says

- **The 150 mm rule removes the bell's peak, but not its edges.** Around
  every peak, 5,000–7,000 pixels per corrupted frame are off by 10–150 mm and
  pass: on 11 October, 88 mm where the truth is 9.
- **It also hurts the clean frame that follows a corrupted one**: Gran Sasso
  becomes "not measured" although the frame is right. A rule that compares two
  frames cannot tell which of the two is wrong.
- **Nothing decides on the whole frame.** A corrupted :30 frame correlates
  0.04–0.33 with the :00 one beside it; a frame-level check would drop it
  whole, and keep the clean one.
- **A stuck gauge is invisible to a jump rule**: it does not move between
  frames. Only `quality` removes it, because it looks at the radar alone: the
  «Merging» reads 126.6 mm, the radar 0, and the radar sees that valley well.
- **Two faulty gauges pass every method.** One is stuck at 7.2 mm, one reads
  1 to 13 mm for a week, both with a dry radar. `quality` asks for 30 mm
  before it calls a patch a gauge fault, because under that a single frame
  cannot tell a gauge from light rain the radar missed. What gives them away
  is the series of the place: the same value to the hundredth 27 times, rain
  in 58 frames in a row. No method here reads a series yet.
- **It does not pay for it with real rain.** Melito, the band off Sardinia
  and Lipari stay as they are. Lipari is the hard one: 31 mm in the «Merging»
  and nothing on the radar, like a faulty gauge, but there the radar is blind
  and the rule stays silent.
- **The "calm" days were not all calm.** On 4 and 6 October `quality` masks
  193 and 59 pixels (0.02 % and 0.01 % of the frame): two more places where
  the «Merging» reads 67–77 mm under a radar that reads nothing.
- **The radar-only frames are clean.** No method breaks the three `SRT1`
  cases, and in 3,838 hourly frames from June to November 2026 the highest
  value is 204 mm in an hour, in a summer storm: nothing like the bell.
- **The 7,348 mm "isolated pixel"** that Limen's 3×3 median was meant to
  remove is the peak of the corrupted bell: its 3×3 neighbours have a median
  of 6,381 mm. The median does not remove it; the 150 mm rule does.
- **Without a map nobody knows where the radar is blind.** With
  `dpc_radar.reliability` Lipari scores 0.42: the lowest beam that reaches it
  passes 3,500 m above the island, over the top of a low cloudburst. Melito,
  60 km away, where the radar measured what the gauges did, scores 0.90: a map
  that called everything unreliable would not pass.

### Checked on whole frames, not only on the cases

Fourteen cases are few, and they are cropped. `benchmarks.season` runs a method on
every pair of whole frames in a folder. On the 96 pairs of CUM24 frames kept
from 25 September to 4 November 2026, `jump-sign` rejects **44 frames, all at
:30, all on the days the :30 series is known to be corrupted**, and **no frame
at :00**, not even during the Melito storm. It also catches what the daily
check missed: smaller corruptions in the evening of 11 October (:30 frames at
370–430 mm where the :00 ones read ~170) and one :30 frame on 7 October.

```bash
uv run --group bench python -m benchmarks.season .cache/dpc-radar/CUM24 jump-sign
```

**The limits of the jump rule.** It needs the frame before. It assumes a corruption adds rain:
a corruption that removes it would be read the wrong way round. A real
cloudburst of more than 150 mm in 30 minutes on five pixels would be rejected
(that is near the Italian records for 30 minutes). And it cannot see what does
not move between frames: for the stuck gauge it takes the radar alone.

The gauge rule of `quality` was measured the same way, on the 1,270 three-hour
«Merging» frames (`CUM3`) kept from 9 June to 16 November 2026, each against
the sum of its three hourly radar frames. It masks 10,266 pixels in 154
frames: 2.4 % of the pixels with 30 mm or more. They are not spread at random:
they fall in 79 places, and seven come back again and again. One, near
13.985°E 41.685°N, is in more than 30 frames from July to October, often between 06 and
09 UTC, with up to 136 mm under a sky that is dry for 100 km around. Another,
near 10.685°E 44.195°N, is in 16 frames in a row on 4–6 August. The outlier
rule masks 4 pixels in those 1,270 frames and none in 1,280 hourly radar
frames; no value is beyond the Italian record. A whole frame takes about
0.17 s.

**The limits of the gauge rule.** We have no gauge readings: that a place is a
faulty gauge is what the radar says, confirmed by hand only for the stuck
gauge of 6–8 October. A radar that is on but reads 0 under real rain, where
the map says it sees well, would make the rule remove real rain: the
`visibility` and `coverage` of the map are there to avoid it, and they
describe 2026. Where the radar is blind a faulty gauge is kept. A gauge that
reads too little is not seen at all. And there is no trained model: fourteen
cases are too few to train one, so these are rules with thresholds that can
be read in [`quality.py`](https://github.com/agent-engineering-studio/dpc-radar/blob/main/src/dpc_radar/quality.py).

### Two cases we had read wrong

Both were *suspect* until November 2026. The hourly radar frames (`SRT1`) and
the 3-hour «Merging» (`CUM3`) of the same days, kept in a local archive,
settled them without a rain gauge.

- **The steady bump is a stuck rain gauge.** `CUM3` reads 126.6 mm at the same
  point in every window from 6 October 12 UTC to 7 October 09 UTC (126.62,
  126.63, 126.63, 126.79 in the last four) and 114–143 mm until 21 UTC: about
  1,400 mm in 33 hours. The radar alone reads 0.0 in all those hours, and on
  8 October the point is back to 0. The «Merging» spread that gauge over more
  than 350 km². So the gauges can be wrong too, not only the radar.
- **The Sardinia disks are rain.** We took the round fields at sea for sea
  clutter. Hour by hour the radar shows a band of rain crossing from west to
  east between 10 and 15 UTC: 19, 22 and 2 mm at the point of the case, 0
  before and after. The circles are the edges of the radars' range, where the
  mosaic ends. The case now asks the opposite: a method must **keep** that
  rain. Over the sea there is no gauge, so the amount is the radar's own.

## Run it

```bash
uv sync --group bench
uv run --group bench python -m benchmarks.run                 # every method
uv run --group bench python -m benchmarks.run limen-max-step  # one method
uv run --group bench python -m benchmarks.run --markdown      # the table above
```

CI runs `python -m benchmarks.run --check`: it fails when an outcome differs
from [`results.json`](results.json), so a change in a case or a method is
always a visible decision. After an intended change, run it with `--record` and
commit `results.json`.

## An experiment: the cases as pictures

The rules above see numbers, not shapes. [`vision.py`](vision.py) asks whether
a model that looks at the map sees more: it draws every case as the frame
before, the frame and the radar alone, sends the picture to the Claude API,
and scores the answer on the same checks.

```bash
uv run --group vision python -m benchmarks.vision render out/  # the pictures: no key
uv run --group vision python -m benchmarks.vision ask          # call the API, save the answers
uv run --group vision python -m benchmarks.vision score        # the table, from the answers
```

`ask` needs `ANTHROPIC_API_KEY` and spends money (14 requests, one picture
each). The answers are saved in `vision-answers.json`, one set per model, so
`score` is free and repeatable. The model gets the picture and the name of the
product: not the title of the case, not its date, not a number above the
500 mm where the scale stops. It does not answer "can the radar be trusted
here?", so its ceiling was 28 of the 30 checks of that day. It is not in `METHODS` and CI does not run
it: it needs a key, and the same picture can get two answers on two days.

**The first run** (18 November 2026, `claude-opus-5-5`, 14 requests, 31,542
tokens in and 7,612 out): **26 of 30**, of the 28 it can reach.

- It rejected the three corrupted frames and kept the clean one after them.
- It masked the stuck gauge in both frames, with the right reason: a smooth
  patch the radar alone does not see.
- It kept the rain west of Sardinia, round edges included, and every frame of
  the Melito storm.
- **It failed the two calm days**, where it masked 0.59 % and 0.13 % of the
  pixels (the limit is 0.1 %). What it masked are four smooth patches of the
  «Merging», up to 67, 66, 26 and 27 mm in 24 hours, where the radar alone
  reads 0.0 on every pixel.
- In the clean frame after the bell it also boxed a patch of 222 mm that the
  case does not ask about.

**Checked on the 3-hour frames** (580 frames of `CUM3` and `SRT1`, 29 September
to 13 October and 23 to 27 October 2026): the patches are three rain gauges
gone wrong, and the two "calm" days are not calm.

| Where | What the «Merging» (`CUM3`) says | The radar alone, same hours |
|---|---|---|
| 13.725 E, 42.365 N | 7.2 mm in each of 27 blocks in a row, from 2 October 12 UTC to 5 October 18 UTC; then 14 to 15 mm a block until 7 October 06 UTC | 0.0 |
| 13.985 E, 41.685 N | bursts with no pattern, up to 130 mm in 3 hours (2 October 06 UTC), from 29 September to at least 26 October: this is also the 222 mm of 11 October | 0.0 in the bursts |
| 15.385 E, 41.215 N | rain in every block for a week, 58 in a row, 1 to 13 mm, from 29 September 09 UTC to 6 October 12 UTC, then nothing | 0.0 |

The three are now cases of the benchmark, so `score` lists them as not asked:
the saved answers are older than them. `quality` removes the second and lets
the other two pass, as the table above says.

The known stuck gauge (13.795 E, 41.805 N) was already wrong two days before
the date of its case: 2.1 mm a block from 4 October 06 UTC.

`quality` already removes the heart of the first two, where the «Merging» has
30 mm or more (`GAUGE_MIN_MM`): 193 pixels on 4 October, 59 on the 6th. It
keeps what is under 30 mm: the rim of those patches, and the whole of the
third. A fault of 3 mm a block, every block for a week, is 26 mm in a day and
no threshold on one frame sees it; what gives it away is that it never stops.
That is a rule on the series, not on the frame, and it is not written yet.
Nor are these three cases in `cases.toml` yet.

So the picture did not beat the rules on the cases we knew, and it pointed at
three faults we had not looked at. That is the use it is good for here:
proposing cases, which a person then confirms on the numbers.

## How faithfully a text quotes the facts

The tools of the MCP server answer with facts; a model turns them into words.
[`faithful.py`](faithful.py) measures what it does with the numbers. The set,
[`faithful.json`](faithful.json), holds five questions on the night of Melito
(the rain at a point over one and three hours, over a box, how rare it is, the
report of the day), the facts the tools gave, recorded once from the archive,
and answers. For every number of an answer the set says what the sentence
says it is (a path into the facts), and the score is arithmetic:

| Kind | The number, against the fact it is said of |
|---|---|
| `faithful` | the fact, to the decimals written |
| `question` | not a fact: it repeats the question |
| `rounded` | within one unit of the last digit, and not the fact (118.1 for 118.04) |
| `swapped` | another figure of the same facts |
| `invented` | none of these |

```bash
uv run python -m benchmarks.faithful score --check          # free, no network
uv run --group vision python -m benchmarks.faithful ask     # a model writes (API key)
uv run --group vision python -m benchmarks.faithful label   # a second model reads
```

Ten answers are written by hand, one faithful and one with known faults per
question; `score --check` fails if they do not score as written, and runs in
CI. Today:

| Author | Answers | Numbers | faithful | rounded | swapped | invented | faults `check_text` misses |
|---|---|---|---|---|---|---|---|
| hand, faithful | 5 | 46 | 46 | 0 | 0 | 0 | - |
| hand, with faults | 5 | 40 | 23 | 4 | 8 | 5 | 8 of 17 |

`check_text` refuses the invented and the badly rounded numbers, and lets
every swapped one through: that is what the set is for. `ask` adds the
answers of a model; `label` asks a second model what each of its numbers is
said of, and reads the hand answers too, so the table says how often that
reader agrees with the hand. No model has answered yet: there was no key
when the set was made. As in Limen's reviewer (`fedelta`), the model only
says what a number is about; whether it is right is arithmetic.

## Add a method

A method answers three questions about a frame and the one before it
(`radar` is the radar-only rain over the same window, for the cases that have
`radar = true`; `None` for the others)
(see `Method` in [`bench.py`](bench.py)):

```python
@dataclass(frozen=True)
class MyCheck:
    name: str = "my-check"
    description: str = "what it does, in one line"

    def frame_ok(
        self, frame: Frame, previous: Frame, radar: Frame | None
    ) -> bool | None: ...  # is the frame usable at all? None: no decision

    def clean(
        self, frame: Frame, previous: Frame, radar: Frame | None
    ) -> np.ndarray: ...  # the frame with untrusted pixels set to NaN

    def reliable(
        self, lon: float, lat: float
    ) -> bool | None: ...  # can the radar be trusted here? None: no decision
```

Register it in `METHODS` in [`methods.py`](methods.py), run it, and compare.
Points are read as Limen reads them: the median of the valid 3×3 pixels.

## Add a case

1. Add a `[[case]]` to [`cases.toml`](cases.toml): the product, the frame
   time, the area, where the truth comes from, and its checks. Mark it
   `suspect` until the answer is confirmed (rain gauges, the news, the DPC,
   the radar-only frames of the same hours).
   With `radar = true` the case also gets the radar-only rain of the same
   window (for a «Merging» product).
2. `uv run --group bench python -m benchmarks.fetch` downloads and crops it
   (only frames younger than about five months still exist).
3. Run the benchmark, check the outcomes, `--record`, commit.
