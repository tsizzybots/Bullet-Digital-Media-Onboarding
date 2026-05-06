# Bullet Digital Media monorepo - convenience targets
# Single entrypoint covering both pnpm (TS) and uv (Python) stacks.

.PHONY: install lint typecheck build test precommit-install precommit-run \
        api-test dashboard-build clean help

help:
	@echo "Available targets:"
	@echo "  install            Install JS (pnpm) and Python (uv) deps"
	@echo "  precommit-install  Install the local git pre-commit hook"
	@echo "  precommit-run      Run all pre-commit hooks against all files"
	@echo "  lint               Run linters across both stacks"
	@echo "  typecheck          Run TS typecheck across all workspace packages"
	@echo "  build              Build all workspace packages"
	@echo "  test               Run JS + Python test suites"
	@echo "  clean              Remove build artefacts and caches"

install:
	pnpm install
	uv sync --all-packages

precommit-install:
	uvx pre-commit install

precommit-run:
	uvx pre-commit run --all-files

lint:
	pnpm -r --if-present lint
	uv run ruff check apps/api

typecheck:
	pnpm -r --if-present typecheck

build:
	pnpm -r --if-present build

test: api-test
	pnpm -r --if-present test

api-test:
	uv run pytest apps/api -q

clean:
	find . -type d -name "node_modules" -prune -exec rm -rf {} +
	find . -type d -name ".next" -prune -exec rm -rf {} +
	find . -type d -name "dist" -prune -exec rm -rf {} +
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -prune -exec rm -rf {} +
