.PHONY: help check lint format typecheck test build docs notebooks diagrams init data demo docker down

help:
	@echo "make check      lint, types and tests: the gate CI runs"
	@echo "make lint       ruff check and format check"
	@echo "make format     format and fix what ruff can fix"
	@echo "make typecheck  mypy --strict"
	@echo "make test       pytest with coverage"
	@echo "make build      the wheel and the sdist"
	@echo "make docs       the site, llms.txt and the agent skill"
	@echo "make notebooks  run every notebook again and save the results (needs the network)"
	@echo "make diagrams   draw the figures of the docs again"
	@echo "make init       install everything and download the data of the demo (needs uv)"
	@echo "make data       download the newest frames again, into ./data"
	@echo "make demo       the demo, on http://localhost:3000 (needs uv and Node)"
	@echo "make docker     the same with Docker alone: build the images, run the demo"
	@echo "make down       stop the demo and remove its containers (the frames stay)"

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

# The demo, on http://localhost:3000 (needs uv and Node).
demo:
	sh scripts/demo.sh

# The same with Docker alone: build the images, run the demo.
docker:
	docker compose up --build

# Stop the demo and remove its containers (the frames downloaded stay).
down:
	docker compose --profile tools down --remove-orphans
