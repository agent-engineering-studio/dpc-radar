# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- The demo is a map, no longer a Streamlit page: all of Italy on OpenStreetMap,
  centred on Rome, with the rain of now or of the last 1 to 24 hours, the alert
  zones, the storms and the radars. A click on a point, a zone, a storm or a
  radar opens a card with the rain there, the forecast of the next two hours,
  the official alert and how far the radar can be trusted. It is two parts: a
  small API over the library (`demo/api.py`, Starlette) and the page
  (`demo/web`, Next.js and Leaflet). `docker compose up` and `make demo` serve
  it on http://localhost:3000 (it was 8501); `make demo` now needs Node too.
  The demo reads live only: `docker compose up` no longer downloads frames
  first (`docker compose run --rm init` still does).
- The map of the demo opens on Italy as large as the window holds it, and the
  radars are drawn only when ticked.

### Added

- The demo plays how the rain moved: the last thirteen frames of the layer
  shown, with a slider to stop on one (`/api/film/{layer}` in `demo/api.py`).
- The demo is in Italian and in English: it opens in the language of the
  browser, and a switch changes it.
- The containers of the demo have fixed names: `dpc-radar-api` and
  `dpc-radar-web`.

## [0.3.0] - 2026-11-17

### Added

- `dpc_radar.reliability(lon, lat)`: how much to trust the radar at a point, as
  a score between 0 and 1 and its parts (`visibility`: the height of the lowest
  beam that clears the mountains; `coverage`: how often the mosaic has data;
  `agreement`: radar alone against the «Merging»). The map ships with the
  package (720 kB, 1 km); `reliability_map().like(grid)` gives the score on the
  pixels of any frame, `SITES` the 24 radars of the mosaic. Lipari, where the
  radar missed the cloudburst of 10 October 2026, scores 0.42; the benchmark
  method `jump-sign-map` passes 19/19 confirmed checks (#3).
- `RainGrid.quality(previous=None, radar=None)`: what to trust in a frame,
  pixel by pixel, and why. A `Quality` with the cleaned grid, a mask, a score
  and the pixels named by each rule: `jump` (the frame before, as `check`),
  `beyond_record` (more than the Italian record for the window), `outlier` (a
  pixel far above its neighbours) and `gauge_only` (in a «Merging» frame, rain
  the radar alone did not see where it sees well: a rain gauge gone wrong).
  `radar_total(frames)` sums the hourly `SRT1` frames for the last rule. Rules
  with measured thresholds, not a trained model. On the benchmark it passes
  30/30 checks; on 1,270 `CUM3` frames it finds faulty gauges in 154 (#6).
- `dpc_radar.correct(grid)`: a radar-only frame (`SRI`, `SRT1`) multiplied by
  the factor «Merging» / radar of every pixel; `radar_factor(lon, lat)` and
  `bias_map()` give the factor. The map ships with the package (110 kB, 1 km):
  the rain of `CUM3` over the rain of the three `SRT1` hours of the same
  window, June to November 2026, over 9 km, on land. From June to November
  2026 the radar alone read 20 % more rain than the «Merging» and passed 30 mm
  in three hours 1.70 times as often; with the map of the other months, the
  mean error of a month goes from 1.91 to 1.55 mm and 30 mm is passed 0.89
  times as often. It lowers heavy rain and is not applied to the 2010–2025
  history: the limits are in the module's documentation.
  `tools/build_bias.py` builds and measures it (#8).
- `dpc_radar.rarity(window, lon, lat, mm)`: how rare a rain of 1, 3, 6 or 24
  hours is at a place, as a percentile and as a return period with its 90 %
  interval; `return_level(window, lon, lat, years)` is the rain of a return
  period and `return_period(grid, window=None)` the return period of every
  pixel of an accumulation frame. The map ships with the package (1.6 MB,
  5 km cells): a GEV of the highest window of the year, from the IT-DPC-SRI
  history of 2018–2025. 100 mm in 24 hours come every 1.7 years in Genoa and
  every 32 in Bari. It is in radar millimetres and stands on eight years: on
  held-out years its "10 years" came about every 7 and its "20 years" every
  13, and nothing is given beyond 50. `tools/build_climatology.py` builds and
  measures it (#4).
- `dpc_radar.storms(grid, threshold=)`: the patches of rain of a frame as
  objects (`Storm`: centre, area, peak, mean, box), and `track(grids,
  threshold=)` to follow them from frame to frame (`Track`: duration, path,
  distance, speed, heading, water). They need SciPy: the new extra `storms`.
- `dpc_radar.rare_events(frames)`: the rain of hourly frames, summed over 1,
  3, 6 and 24 hours, that is over the level of its place and where the radar
  is reliable, as `Event`s ranked the rarest first; `with_merging(event,
  archive)` adds what the «Merging» has there and whether it confirms the
  radar. `tools/build_catalogue.py` writes the list as a CSV: on 160 days of
  2026 it is 1,689 events over the 10-year level on land, 1,054 of them
  confirmed by the «Merging» (`docs/data/rare-rain-2026.csv`). They are
  candidates to check in the news, and the return period is a ranking: on
  those frames the levels are passed two to five times more often than their
  name says (#5).
- `dpc_radar.nowcast(frames)`: the rain of the next two hours, every five
  minutes: the latest SRI frame moved along the motion the rain had in the
  last half hour. `rain_ahead(frames, minutes=)` sums it into millimetres,
  `chance(frames, mm=, minutes=)` gives the chance of at least `mm` at every
  pixel, `motion(frames)` the speed and heading of the rain. All return a
  `Forecast`, never a `RainGrid`. They need SciPy: the new extra `nowcast`.
  On 120 moments of heavy rain of 2021–2025 it beats the last frame left
  still at every threshold and lead (rain of 1 mm/h at one hour: CSI 0.34
  against 0.27; 10 mm/h: 0.08 against 0.05), and it is poor on peaks: the
  rain is moved, not changed. `chance` is reliable (of the pixels given
  40–60 % of 10 mm in the hour, 39 % got them). `tools/measure_nowcast.py`
  measures it (#7).
- `dpc_radar.history.suspect(frame)`: whether a frame of the history is
  corrupted (the disk of a radar filled with violent rain, flat patches over
  100 mm/h): 752 of 1,039,785 frames, 229 of them in 2011 and 250 in 2014.
- `reliability_map().like(grid, part)` gives any part of the map on the pixels
  of a frame, not only the score.
- Benchmark: a case with `radar = true` also carries the radar-only rain of
  its window, and a method receives it.
- Benchmark: four new cases, three of them on the radar-only `SRT1`, and the
  two suspect cases settled on the hourly frames: the steady 1,152 mm bump is
  a stuck rain gauge (not rain), the "disks" west of Sardinia are a band of
  rain (the case now asks to keep it). 30 checks; the library passes 28 (#2).
- `tools/build_reliability.py` builds the map again from a local archive and
  open terrain tiles.
- Five notebooks in `notebooks/`, saved with their results and runnable in
  Colab: the first rain map, the anatomy of a storm, the frame check, the
  hourly archive, the official alert next to the radar.
- Four example programs in `examples/`: the rain at a place, a storm watch,
  a nightly archive and a Streamlit dashboard. `uv sync --group examples`
  installs what they need.
- The README explains in plain words how a radar measures rain and where it
  fails, the open data behind the library, its benefits and what can be built
  with it.
- `dpc-radar mcp`: an MCP server with eight tools on the rain over Italy
  (`latest`, `rain_at`, `area_summary`, `storm_now`, `rain_ahead`,
  `official_alert`, `rarity`, `radar_status`). Every answer is computed from a frame and
  names its product and time; the frame is checked with `quality` first, and
  "not measured" is `null`, never zero. `dpc_radar.facts.Facts` gives the
  same answers from Python. New extra `mcp` (#9).
- `dpc-radar archive FOLDER --days 2`: the frames of the last days, saved
  before the DPC deletes them. The command-line face of
  `dpc_radar.archive.archive`: it fetches what the folder lacks and says how
  it went in one line.
- A demo in `demo/`: one Streamlit page with a tab for each thing the library
  does (the rain at a place, the storms now, the next two hours, how rare a
  rain is, how far the radar can be trusted, the report of a day), each with
  the code that made it and the data it returned. It replaces
  `examples/app.py`.
- `scripts/init.sh` and `scripts/fetch-data.sh`: install everything with uv
  and download what the demo reads from disk (two days of frames, the borders
  of the maps) into `./data`.
- A `Dockerfile` and a `compose.yaml`: `docker compose up` builds an image
  with the library, every extra, the command line and the MCP server,
  downloads the data into a volume and serves the demo on
  http://localhost:8501. The image is built locally; none is published yet.
- `dpc-radar report --bbox W,S,E,N --day YYYY-MM-DD --out folder`: the report
  of a day of rain over a box, from the API or from a local archive:
  `facts.json`, `report.md`, a map of the radar alone next to the «Merging»
  and an hourly chart. Every number of the text is checked against the facts
  before it is saved (`dpc_radar.report.check_text`, `dpc-radar check`), and
  a text of your own with a number that is not there is refused. New extra
  `report` (#9).
- `bulletins.names()` and `bulletins.name_on(names, day)`: the bulletins the
  DPC keeps, and the one issued on a day (#9).
- `RadarClient.radar_status(time=None)`: which radars the DPC viewer reports
  as on, now or at a time of the last two weeks, as a `NetworkStatus`
  (`.off`, `.of(site)`, `.geojson()`), or `ABSENT` when not known. Also
  `dpc-radar sites [--time] [--out FILE]`, the MCP tool `radar_status`,
  `Facts.radar_status` and, in the demo, the radars reported off on the
  reliability map. `RadarSite.code` links `SITES` to the file. The file is
  public but not documented and has no licence, and it does not always
  agree with the mosaic (17 November 2026: Linate reported off with data
  all around it, Catania reported on with none), so it is a hint and never
  enters `reliability` or `quality`. The 23 radars the file shares with
  `SITES` are within 1.4 km of our positions; it names three more (Reggio
  Calabria, Desio, Flero) that leave no trace in the mosaic.
- `llms.txt`, `llms-full.txt` and an agent skill (`skills/dpc-radar/SKILL.md`),
  published with the documentation (#9).

### Changed

- The documentation presents dpc-radar as a library on its own, no longer as
  a part being extracted from Limen. What is still open on Limen's side is
  tracked in limen#643.

- `SriHistory.hourly` reads a corrupted frame as missing.

- The documentation site is built with Zensical (`zensical.toml`) instead of
  MkDocs Material (`mkdocs.yml`); pages and URLs are unchanged.

### Fixed

- `RainGrid.crop` raised `TypeError` with affine 2 (the version rasterio 1.4
  installs by default), which has no `@` operator. It now works with affine 2
  and 3.

## [0.2.1] - 2026-11-05

### Added

- `bulletins.latest`, `fetch` and `latest_name` take a `send=` hook, like
  `RadarClient`.
- The `dpc_radar.archive` logger says which frames failed or could not be read,
  and why. The signed download link is never logged.

### Fixed

- `MergingArchive` can be shared between threads. Its cache was not locked:
  under heavy thread switching, `read()` raised `KeyError` and could crash the
  interpreter with a segmentation fault.
- `MergingArchive.read` treats an `OSError` (no permission, a file removed while
  reading) as one unreadable frame, instead of losing the whole `hourly()` call.
- `RadarClient` and `bulletins` raise `DpcRadarError` when a 200 answer is not a
  JSON object, such as a proxy page or a body cut off. `archive()` now counts
  that as one failed frame instead of ending the run.
- `archive()` counts an `OSError` from a download, `TimeoutError` included, as
  one failed frame. The `send` hook contract is documented.

## [0.2.0] - 2026-11-05

### Added

- `archive`: `archive()` downloads the CUM3 and SRT1 frames an archive on disk
  lacks (oldest first, frames that are gone written to `absent.txt`, idempotent;
  reads Limen's layout and `assenti.txt`); `MergingArchive.hourly` gives the
  rain of every hour at lon/lat points, SRT1 rescaled so that each block adds
  up to its CUM3; `split_block`.
- `history` (extra `history`, `numcodecs`): `SriHistory` reads the IT-DPC-SRI
  Zarr store (2010-2025) with no zarr or xarray; `hourly` with the same
  signature as `MergingArchive.hourly`.
- `bulletins` (extra `bulletins`, `shapely`): the DPC criticality bulletin
  from its GitHub repository, today and tomorrow: `latest`, `fetch`,
  `latest_name`; each `Zone` with its levels (map, hydro-geological,
  hydraulic, thunderstorms; 0-3), municipalities and shape;
  `Bulletin.alerts()` and `Bulletin.zone_at(lon, lat)`.

### Changed

- `RainGrid.data` is float32, as published: half the memory, and exactly the
  values Limen reads.

### Fixed

- Compared with Limen's reader of IT-DPC-SRI, the first hour of a window now
  counts the frame issued at its start, and no hour after the window is
  returned.

## [0.1.0] - 2026-11-04

### Added

- `products`: the DPC radar products (SRI, SRT1, CUM3/6/12/24), the requests of
  `findLastProductByType` and `downloadProduct`, the parsers of their answers,
  and `is_expired` for a frame the DPC no longer keeps.
- `DpcRadarError`.
- `grid`: `RainGrid`, one frame as an array (NaN where there is no data), from
  its GeoTIFF; `sample` (median of the 3×3 pixels), `peak`, `crop`, and
  `check(previous, max_step=)`, the frame-level `jump-sign` rule: the frame
  that jumps up is rejected, the one after a corrupted frame kept whole.
  `discard_inconsistent` kept for comparison with Limen.
- `client`: `RadarClient` with an injectable `httpx.AsyncClient`, retries
  with backoff on network errors and 429/5xx, a `send=` hook; `latest`
  (with `cached=`), `at`, `download`, `last_product`; `ABSENT` for a frame the
  DPC no longer keeps, never retried.
- `Product.period`: 5 minutes for SRI and SRT1, 30 for the CUM products.
- Core dependencies: `httpx`, `numpy`, `rasterio`.
- `benchmarks/`: known cases of the DPC radar data (Melito and Lipari on
  10 October 2026, the corrupted :30 CUM24 series, calm days, two suspect
  artefacts), a runner that scores cleaning methods on them, and Limen's
  current rule as a baseline (#2). Checked in CI.
- `benchmarks`: `jump-sign`, a frame-level check (#6): the frame that jumps up
  more than 150 mm in 30 minutes is rejected whole, the one after it kept.
  17/18 confirmed checks (Limen today 8/18); on 96 whole-frame pairs it
  rejects the 44 corrupted :30 frames and no :00 frame.
  `benchmarks.season` runs a method on every pair of whole frames.

### Documentation

- README rewritten for a wider audience: what the DPC radar is, the products,
  a quick start, the traps, with maps and charts of the 10 October 2026 storm
  in Reggio Calabria drawn from real frames by `docs/make_images.py`.
- Documentation site (MkDocs Material) with the API reference, published to
  GitHub Pages by the `docs` workflow.
- Roadmap with an analysis of where AI and machine learning can improve the
  library, and through it Limen.
