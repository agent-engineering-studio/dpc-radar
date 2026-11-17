# Radar status

Which radars the DPC viewer shows as on, at a time. Use it to explain why a
place has no data; do not use it to say that the data are good.

```python
from dpc_radar import RadarClient

async with RadarClient() as radar:
    status = await radar.radar_status()  # the newest, or radar_status(time)
    if status:  # ABSENT: not known
        print(status.time, [r.code for r in status.off])
        print(status.of("Monte Lauro"))  # True, False, or None: not in the file
```

```bash
dpc-radar sites                          # a table, now
dpc-radar sites --time 2026-11-17T12:00  # at a time (UTC), for about two weeks back
dpc-radar sites --out sites.geojson      # as GeoJSON
```

**Read this before you rely on it.**

- The file is the one the DPC radar viewer reads. It is public, but it is not
  documented, it has no licence, and its address may change without notice.
  When it is not there, the answer is `ABSENT`: not known.
- It is a hint. On 17 November 2026 the two radars reported off in the south
  (Monte Lauro, Monte Pettinascura) were indeed missing from the mosaic. But
  Milano Linate was reported off for two weeks while the mosaic had data over
  its whole circle, and Catania was reported on while its circle had none.
- It names three radars that are not in `SITES` (Reggio Calabria, Desio,
  Flero): nothing in the mosaic shows that they feed it. They are listed with
  `site` empty. Salento is in `SITES` and not in the file.
- Its positions are rounded and some altitudes differ from those of `SITES`,
  which are kept: `RadarStatus.site` links the two through `RadarSite.code`.
- Files are kept for about two weeks, one every five minutes.

::: dpc_radar.status
