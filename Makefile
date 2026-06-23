.PHONY: help venv install install-dev test lint format check release-check source-release run docker-build

help:
	@echo "Targets:"
	@echo "  install       Install runtime deps"
	@echo "  install-dev   Install runtime + dev deps"
	@echo "  test          Run pytest"
	@echo "  lint          Run ruff"
	@echo "  format        Run black + ruff --fix"
	@echo "  check         Run lint, format check, tests, and release checks"
	@echo "  source-release Build a private-data-safe source ZIP"
	@echo "  run           Run Flask dev server"
	@echo "  docker-build  Build Docker image"

install:
	python -m pip install -r requirements.txt

install-dev: install
	python -m pip install -r requirements-dev.txt


test:
	pytest

lint:
	ruff check .

format:
	black .
	ruff check . --fix

check: lint test release-check
	black --check .

release-check:
	python scripts/check_release_clean.py

source-release: release-check
	python scripts/build_source_release.py

run:
	flask --app vortnotes:create_app run --debug

docker-build:
	docker build -t vortnotes:latest .
