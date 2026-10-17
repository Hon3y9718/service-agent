.PHONY: install dev test db-up db-down clean

install:
	uv sync
	cd web && npm install

dev: db-up
	trap 'kill 0' EXIT; uv run uvicorn main:app --reload & cd web && npm run dev

test:
	uv run pytest -v

db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

migration:
	uv run alembic revision --autogenerate -m "$(m)"

migrate:
	uv run alembic upgrade head

update-db:
	uv run alembic revision --autogenerate -m "$(m)"
	uv run alembic upgrade head

clean:
	rm -rf data logs .pytest_cache
	find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +