# Notebooks

Five notebooks that tell what `dpc-radar` does, from the first map to the
official alert. Each one is saved **with its results**: read it here like an
article, or run it.

| | Notebook | What it shows | Data | Run it |
|---|---|---|---|---|
| 1 | [Your first rain map of Italy](01_first_rain_map.ipynb) | The latest frame, the rain on your city, and why «no data» is not «no rain». | live: today's rain | [Colab](https://colab.research.google.com/github/agent-engineering-studio/dpc-radar/blob/main/notebooks/01_first_rain_map.ipynb) |
| 2 | [Anatomy of a storm](02_anatomy_of_a_storm.ipynb) | The flood of Melito di Porto Salvo (10 October 2026) hour by hour, and what the radar alone lost. | live: frames kept by the DPC until about March 2027 | [Colab](https://colab.research.google.com/github/agent-engineering-studio/dpc-radar/blob/main/notebooks/02_anatomy_of_a_storm.ipynb) |
| 3 | [Trust, but verify](03_trust_but_verify.ipynb) | A published frame with 7,348 mm of false rain, and the check that rejects it. | offline: frames saved in `benchmarks/` | [Colab](https://colab.research.google.com/github/agent-engineering-studio/dpc-radar/blob/main/notebooks/03_trust_but_verify.ipynb) |
| 4 | [Hourly rain from your own archive](04_hourly_rain_archive.ipynb) | Save the frames before they are gone, then read rain per hour corrected with the gauges. | live: until about March 2027; writes `notebooks/data/` | [Colab](https://colab.research.google.com/github/agent-engineering-studio/dpc-radar/blob/main/notebooks/04_hourly_rain_archive.ipynb) |
| 5 | [The official alert, next to the radar](05_alert_vs_radar.ipynb) | What the bulletin announced and what fell, zone by zone. | live: bulletin always; radar until about March 2027 | [Colab](https://colab.research.google.com/github/agent-engineering-studio/dpc-radar/blob/main/notebooks/05_alert_vs_radar.ipynb) |

## Run them on your computer

```bash
git clone https://github.com/agent-engineering-studio/dpc-radar
cd dpc-radar
uv sync --group examples
uv run --group examples jupyter lab notebooks    # or open them in VS Code
```

Without `uv`: `pip install "dpc-radar[bulletins]" matplotlib jupyterlab`.

To run all of them again and save the new results: `make notebooks`.

## Good to know

- Notebooks 2, 4 and 5 read the frames of 10 October 2026 from the DPC API,
  which keeps about five months. After **March 2027** they will say «frame no
  longer kept»: change the date at the top to a recent storm, and they work
  again. This is the very reason notebook 4 exists.
- Notebook 4 writes about 35 MB of frames in `notebooks/data/` (ignored by
  git).
- No account, no key. The data is © Dipartimento della Protezione Civile,
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/): if you
  share a map, credit the DPC.
- A map of measured rain is not a forecast and not an alert. For official
  warnings, see the DPC bulletin and your regional civil protection.

## Share what you make

Made a map of the storm over your town? Post it with `#dpcradar`, and tell
us in the [discussions](https://github.com/orgs/agent-engineering-studio/discussions):
we would like to see it.
