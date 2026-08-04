.PHONY: install run api test lint fmt migrate

install:
	pip install -e ".[dev]"

run:
	python -m app.main_bot

api:
	uvicorn app.main_api:app --host 0.0.0.0 --port $${API_PORT:-8000}

test:
	pytest -q

lint:
	ruff check app tests

fmt:
	ruff check --fix app tests

migrate:
	python -m migrations.runner
