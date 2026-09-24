# Everything a reviewer needs, in the order they would run it.
#
#   make setup      dependencies
#   make db         a local PostgreSQL
#   make corpus     the synthetic fixtures, from a committed seed
#   make artifacts  the eight evidence files
#   make test       the whole suite, kill criteria included
#   make console    the operator console on http://127.0.0.1:8061
#
# `make evidence` is the whole chain and is what CI runs.

.PHONY: help setup db db-down corpus determinism artifacts residency test fast lint types \
        breaches console evidence clean

PY := .venv/Scripts/python.exe
ifeq ($(OS),)
PY := .venv/bin/python
endif

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## install dependencies into .venv
	uv sync

db: ## start a local PostgreSQL on 127.0.0.1:15437
	docker compose up -d postgres

db-down: ## stop it
	docker compose down

migrate: ## bring the schema up to head
	$(PY) -m alembic upgrade head

corpus: ## generate the synthetic corpus from the committed seed
	$(PY) scripts/generate_corpus.py

determinism: ## build the corpus twice and diff every byte
	$(PY) scripts/generate_corpus.py --verify-determinism

residency: ## regenerate the committed residency manifests
	$(PY) scripts/residency_manifest.py

artifacts: ## build the eight evidence artifacts (needs a database)
	$(PY) scripts/build_artifacts.py

artifacts-check: ## fail if the committed evidence no longer matches a fresh build
	$(PY) scripts/check_artifacts_current.py

lint: ## ruff
	$(PY) -m ruff check src tests scripts
	$(PY) -m ruff format --check src tests scripts

types: ## mypy --strict
	$(PY) -m mypy src

fast: lint types ## everything that needs no infrastructure
	$(PY) -m pytest tests -q --ignore=tests/test_kill_criteria.py

test: ## the whole suite, kill criteria included
	$(PY) -m pytest tests -q

breaches: ## plant nineteen defects and check each is caught
	$(PY) scripts/plant_breaches.py

console: ## serve the operator console
	$(PY) -m uvicorn bordereaux_reconciler.api.app:app --port 8061 --reload

evidence: corpus determinism migrate artifacts test ## the full chain CI runs
	@echo
	@echo "evidence rebuilt and graded against the predeclared thresholds"

clean: ## remove generated fixtures and caches
	rm -rf data/generated .pytest_cache .mypy_cache .ruff_cache
