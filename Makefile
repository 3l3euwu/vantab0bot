.PHONY: help install dev run test lint format check docker docker-run clean

help:
	@echo "Vantab targets:"
	@echo "  make install   Create .venv and install runtime dependencies"
	@echo "  make dev       Install runtime + development dependencies"
	@echo "  make run       Start the bot"
	@echo "  make test      Run the test-suite"
	@echo "  make lint      Run ruff"
	@echo "  make format    Auto-fix lint issues"
	@echo "  make check     Lint + tests"
	@echo "  make docker    Build the Docker image"
	@echo "  make clean     Remove caches"

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip

install: .venv/bin/python
	.venv/bin/pip install -r requirements.txt

dev: .venv/bin/python
	.venv/bin/pip install -r requirements-dev.txt

run:
	.venv/bin/python bot.py

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check .

format:
	.venv/bin/ruff check . --fix

check: lint test

docker:
	docker build -t vantab .

docker-run:
	docker compose up -d --build

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__
