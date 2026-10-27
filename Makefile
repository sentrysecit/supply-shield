.DEFAULT_GOAL := help
.PHONY: help install run lint format typecheck test test-integration cov security check up down clean demo

AUDIT_REQUIREMENTS := requirements-audit.txt

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies and Git hooks
	uv sync
	uv run pre-commit install

run: ## Run the API locally with auto-reload
	uv run uvicorn supply_shield.main:create_app --factory --reload

lint: ## Lint and check formatting
	uv run ruff check
	uv run ruff format --check

format: ## Auto-fix lint issues and format the code
	uv run ruff check --fix
	uv run ruff format

typecheck: ## Run mypy in strict mode
	uv run mypy

test: ## Run unit tests
	uv run pytest -m "not integration"

test-integration: ## Run integration tests
	uv run pytest -m integration

cov: ## Run all tests with coverage (fails under 80%)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

security: ## Run bandit and audit the locked dependencies
	uv run bandit -c pyproject.toml -r src
	uv export --locked --no-emit-project --format requirements-txt --output-file $(AUDIT_REQUIREMENTS) --quiet
	uv run pip-audit --requirement $(AUDIT_REQUIREMENTS) --require-hashes --disable-pip

check: lint typecheck security cov ## Run everything CI runs

up: ## Build and start the container
	docker compose up -d --build

down: ## Stop the container
	docker compose down

clean: ## Remove caches and generated artifacts
	uv run python -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for pattern in ('.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', 'dist', '**/__pycache__') for p in pathlib.Path().glob(pattern) if '.venv' not in p.parts]; [p.unlink() for p in map(pathlib.Path, ('.coverage', 'coverage.xml', '$(AUDIT_REQUIREMENTS)')) if p.exists()]"

demo: ## Run the end-to-end demo script
	uv run python scripts/demo.py
