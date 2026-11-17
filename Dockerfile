# dpc-radar with every extra and the demo: the library, the command line, the
# MCP server and a Streamlit page, ready to run.
#
#   docker compose up            the demo, on http://localhost:8501
#   docker build -t dpc-radar:local .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

# rasterio brings its own GDAL, which needs libexpat from the system.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    DPC_RADAR_DATA=/data \
    DPC_RADAR_RUN=

WORKDIR /app

# The dependencies first: this layer is rebuilt only when they change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --all-extras --group demo --no-install-project

COPY README.md LICENSE ./
COPY src src
RUN uv sync --locked --no-dev --all-extras --group demo --no-editable

# Fail the build, not the first run, if a system library is missing.
RUN python -c "import rasterio, scipy, shapely, mcp, streamlit, dpc_radar"

COPY demo demo
COPY scripts scripts

RUN useradd --create-home radar && mkdir /data && chown radar /data
USER radar
VOLUME /data

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"]

CMD ["streamlit", "run", "demo/app.py", "--server.address", "0.0.0.0", "--server.headless", "true", "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal"]
