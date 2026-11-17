# Storms as objects

A frame is a field of pixels. `storms` turns it into a list of storms, each
with a centre, an area and a peak; `track` follows them from frame to frame
and says how long each one lived, where it went and how fast.

```python
from datetime import timedelta
from dpc_radar import storms, track  # pip install "dpc-radar[storms]"

srt1 = await radar.latest("SRT1")
for storm in storms(srt1, threshold=20):  # patches of 20 mm in the hour or more
    print(storm.area_km2, storm.lon, storm.lat, storm.peak)
# 58.0 13.32 44.98 31.7        (10 October 2026, 06:00 UTC)

for t in track(hourly_frames, threshold=20, max_gap=timedelta(hours=1)):
    print(t.start, t.end, t.peak, t.area_km2, t.distance_km, t.speed_kmh, t.heading)
# 02:00 06:00 36.0 541.0 30.7 7.7 92.0     the storm over Melito di Porto Salvo
```

With SRI frames every five minutes the default `max_gap` of 20 minutes is the
right one; with hourly frames say `max_gap=timedelta(hours=1)`, and read the
track as "rain stayed here", not as the motion of one cell.

::: dpc_radar.storms
