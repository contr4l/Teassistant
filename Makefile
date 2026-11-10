.PHONY: setup check schemas selection evaluate

setup:
	UV_CACHE_DIR="$(CURDIR)/.cache/uv" uv sync --locked --group export

check:
	.venv/bin/ruff check backend scripts
	.venv/bin/pytest -q

schemas:
	.venv/bin/python scripts/generate_schemas.py

selection:
	.venv/bin/python scripts/selection_demo.py

evaluate:
	.venv/bin/python scripts/evaluate.py
