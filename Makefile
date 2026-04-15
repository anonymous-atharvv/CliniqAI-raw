# CliniQAI Makefile — Developer Workflow
# Usage: make <target>

.PHONY: help dev stop logs test test-unit test-integration lint format migrate seed clean build zip

PYTHON := python3
PIP := pip3
DOCKER := docker compose

help:
	@echo "CliniQAI Developer Commands:"
	@echo ""
	@echo "  make dev          Start full local development stack"
	@echo "  make stop         Stop all services"
	@echo "  make logs         Tail logs from all services"
	@echo "  make migrate      Run database migrations"
	@echo "  make seed         Load synthetic patient data (dev only)"
	@echo "  make test         Run full test suite"
	@echo "  make test-unit    Run unit tests only"
	@echo "  make test-int     Run integration tests (requires services)"
	@echo "  make test-clin    Run clinical validation tests"
	@echo "  make lint         Run ruff + mypy"
	@echo "  make format       Auto-format with black + ruff --fix"
	@echo "  make perf         Run performance tests"
	@echo "  make fhir-check   Validate FHIR R4 compliance"
	@echo "  make clean        Remove __pycache__ and build artifacts"
	@echo "  make build        Build Docker images"
	@echo ""

# ── Local Development ──────────────────────────────────────────────────────────
dev:
	@echo "🚀 Starting CliniQAI dev stack..."
	cd infrastructure && $(DOCKER) up -d
	@echo "⏳ Waiting for services..."
	sleep 8
	@echo "📦 Running migrations..."
	$(MAKE) migrate
	@echo ""
	@echo "✅ Stack ready:"
	@echo "   Backend API:  http://localhost:8000"
	@echo "   API Docs:     http://localhost:8000/docs"
	@echo "   Grafana:      http://localhost:3001  (admin/admin)"
	@echo "   Qdrant UI:    http://localhost:6333/dashboard"
	@echo ""
	@echo "Run: uvicorn backend.main:app --reload"

stop:
	cd infrastructure && $(DOCKER) down

logs:
	cd infrastructure && $(DOCKER) logs -f

# ── Database ───────────────────────────────────────────────────────────────────
migrate:
	$(PYTHON) scripts/migrate_db.py

seed:
	@echo "⚠️  This loads SYNTHETIC data only. Never use on production."
	$(PYTHON) scripts/seed_synthea.py --patients 100 --icu 20 --output dev_data
	@echo "💉 Loading into database..."

# ── Tests ──────────────────────────────────────────────────────────────────────
test:
	$(PYTHON) -m pytest tests/ -v

test-unit:
	$(PYTHON) -m pytest tests/unit/ -v -m "not integration"

test-int:
	$(PYTHON) -m pytest tests/integration/ -v

test-clin:
	$(PYTHON) -m pytest tests/clinical/ -v

perf:
	$(PYTHON) scripts/perf_test.py --scenario all

fhir-check:
	$(PYTHON) scripts/validate_fhir.py --loinc-check

# ── Code Quality ───────────────────────────────────────────────────────────────
lint:
	$(PYTHON) -m ruff check backend/ tests/
	$(PYTHON) -m mypy backend/ --ignore-missing-imports --no-strict-optional

format:
	$(PYTHON) -m black backend/ tests/ scripts/
	$(PYTHON) -m ruff check --fix backend/ tests/

# ── Build ──────────────────────────────────────────────────────────────────────
build:
	docker build -t cliniqai/backend:latest -f backend/Dockerfile .
	docker build -t cliniqai/frontend:latest -f frontend/Dockerfile .

# ── Cleanup ────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dev_data/ dist/ build/ 2>/dev/null || true
	@echo "✅ Cleaned"
