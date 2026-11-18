.PHONY: dev backend ui build test lint demo install

install:
	cd backend && uv sync --locked
	cd frontend && npm ci --no-audit --no-fund

backend:
	cd backend && uv run labkeeper demo --port 8000

ui:
	cd frontend && npm run dev

dev:
	@echo "backend on :8000 (demo) + UI on :5173"
	$(MAKE) -j2 backend ui

build:
	cd frontend && npm ci && npm run build

test:
	cd backend && uv run --frozen pytest -q

lint:
	cd backend && uv run --frozen ruff check --select E9,F labkeeper tests
	cd frontend && npx tsc -b --noEmit

demo: build
	cd backend && uv run labkeeper demo
