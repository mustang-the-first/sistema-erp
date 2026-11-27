install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest --cov --cov-report=term

test-unit:
	uv run pytest -m unit

db:
	docker compose up -d db

migrate:
	uv run alembic upgrade head

run:
	uv run uvicorn app.main:app --reload

up:
	docker compose up --build

check: lint test

.PHONY: install lint format test test-unit db migrate run up check
