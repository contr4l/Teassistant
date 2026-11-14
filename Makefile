.PHONY: setup check setup-web check-web schemas selection evaluate deploy-init deploy-check docker-build docker-build-tools docker-pull docker-up docker-status

setup:
	UV_CACHE_DIR="$(CURDIR)/.cache/uv" uv sync --locked --group export

check:
	.venv/bin/ruff check backend scripts tools
	.venv/bin/pytest -q

setup-web:
	pnpm --dir frontend install --frozen-lockfile

check-web:
	pnpm --dir frontend typecheck
	pnpm --dir frontend build

schemas:
	.venv/bin/python scripts/generate_schemas.py

selection:
	.venv/bin/python scripts/selection_demo.py

evaluate:
	.venv/bin/python scripts/evaluate.py

deploy-init:
	python3 scripts/deploy.py init

deploy-check:
	python3 scripts/deploy.py check

docker-build:
	python3 scripts/deploy.py build

docker-build-tools:
	python3 scripts/deploy.py build-tools

docker-pull:
	python3 scripts/deploy.py pull

docker-up:
	python3 scripts/deploy.py up

docker-status:
	python3 scripts/deploy.py status
