.PHONY: setup seed dev test lint format clean \
	demo-discovery demo-replay demo-duplicate demo-wrong-entity \
	demo-crash demo-handoff demo-second-institution demo-uncertain

setup:
	uv venv --python 3.12
	uv pip install -e ".[dev]"
	uv run playwright install chromium

seed:
	uv run python scripts/seed.py

dev:
	uv run python scripts/start_services.py

docker-build:
	docker build -t tandem-system .

docker-up:
	docker compose up --build

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check .

format:
	uv run ruff format .

demo-discovery:
	uv run python scripts/demo.py --scenario discovery

demo-replay:
	uv run python scripts/demo.py --scenario replay-new-case

demo-duplicate:
	uv run python scripts/demo.py --scenario replay-same-case

demo-wrong-entity:
	uv run python scripts/demo.py --scenario transposed-id

demo-crash:
	uv run python scripts/demo.py --scenario crash-resume

demo-handoff:
	uv run python scripts/demo.py --scenario human-handoff

demo-second-institution:
	uv run python scripts/demo.py --scenario second-institution

demo-uncertain:
	uv run python scripts/demo.py --scenario uncertain-effect

clean:
	rm -rf .pytest_cache .ruff_cache *.db *.sqlite test-results evidence/*.png
