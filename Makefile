.PHONY: check lint format typecheck test build docs notebooks diagrams init data demo docker

check: lint typecheck test

lint:
	uv run ruff check
	uv run ruff format --check

format:
	uv run ruff format
	uv run ruff check --fix

typecheck:
	uv run mypy

test:
	uv run pytest --cov --cov-report=term-missing

build:
	uv build

docs:
	uv run --group docs zensical build --clean --strict
	uv run --group docs python tools/build_llms.py

# Run every notebook again and save the results. Needs the network: 02, 04
# and 05 read frames the DPC keeps for about five months.
notebooks:
	cd notebooks && uv run --group examples jupyter nbconvert --to notebook --execute --inplace *.ipynb

diagrams:
	uv run --group examples python docs/make_diagrams.py

# Install everything and download the data of the demo (needs uv).
init:
	sh scripts/init.sh

# Download the newest frames again, into ./data.
data:
	sh scripts/fetch-data.sh

# The demo, on http://localhost:8501.
demo:
	uv run --all-extras --group demo streamlit run demo/app.py --client.toolbarMode minimal

# The same with Docker alone: build the image, download the data, run the demo.
docker:
	docker compose up --build
