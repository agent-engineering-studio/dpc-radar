#!/bin/sh
# Download what the demo reads from disk: the frames of the last days, which
# the DPC deletes after about five months, and the borders drawn on the maps.
# And the radars with their status of now, as GeoJSON, for whoever wants them
# in a GIS (the demo asks for the status live).
#
#   scripts/fetch-data.sh
#
#   DPC_RADAR_DATA   where to put them (default: ./data)
#   DPC_RADAR_DAYS   how many days of frames (default: 2, about 70 MB)
#   DPC_RADAR_RUN    what to run Python with (default: uv; empty inside Docker)
#
# Safe to run again: what is already there is not downloaded twice. A download
# that fails is a warning, not an error: the demo also works on live data alone.
set -u
cd "$(dirname "$0")/.."

DATA="${DPC_RADAR_DATA:-$PWD/data}"
DAYS="${DPC_RADAR_DAYS:-2}"
RUN="${DPC_RADAR_RUN-uv run --all-extras --group demo}"

mkdir -p "$DATA/archive" "$DATA/cache" "$DATA/reports"

echo "Frames of the last $DAYS days, into $DATA/archive"
$RUN dpc-radar archive "$DATA/archive" --days "$DAYS" ||
    echo "warning: the frames were not downloaded; run this script again later" >&2

echo "Borders for the maps, into $DATA/cache"
$RUN python demo/borders.py "$DATA/cache" ||
    echo "warning: the borders were not downloaded; the maps will be drawn without" >&2

echo "The radars and which are on, into $DATA/cache/sites.geojson"
$RUN dpc-radar sites --out "$DATA/cache/sites.geojson" ||
    echo "warning: the radar status was not downloaded; it is not known right now" >&2

echo "Done."
