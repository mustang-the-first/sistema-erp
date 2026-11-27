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

migrate-check:
	uv run alembic upgrade head
	uv run alembic check

run:
	uv run uvicorn app.main:app --reload

seed-demo:
	uv run python -m scripts.seed_demo

demo-reset:
	ERP_DEMO_MODE=true uv run python -m scripts.reset_demo

up:
	docker compose up --build

check: lint test

.PHONY: install lint format test test-unit db migrate migrate-check run seed-demo demo-reset up check
