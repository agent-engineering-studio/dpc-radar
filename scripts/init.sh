#!/bin/sh
# Set up dpc-radar on this machine, without Docker: install it with every
# extra, download the data of the demo, say what to do next.
#
#   scripts/init.sh
#
# It needs uv (https://docs.astral.sh/uv/), which also installs Python.
set -eu
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. Install it with" >&2
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    echo "or use Docker instead: docker compose up" >&2
    exit 1
fi

echo "Installing dpc-radar with every extra, and what the demo needs"
uv sync --all-extras --group demo

sh scripts/fetch-data.sh

cat <<'NEXT'

Ready. Now:

    make demo                              the demo, on http://localhost:8501
    uv run dpc-radar --help                the command line
    uv run python                          then: import dpc_radar

NEXT
