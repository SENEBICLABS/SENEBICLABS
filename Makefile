# Common tasks. `make` on its own lists them.

.DEFAULT_GOAL := help
.PHONY: help setup test api ui deploy health

help:  ## Show this list
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## Create the venv, install dependencies, copy the env template
	python3.10 -m venv .venv
	./.venv/bin/pip install --upgrade pip
	./.venv/bin/pip install -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "Done. Activate with: source .venv/bin/activate"

test:  ## Run the hermetic suite (no database, no network, no credentials)
	PYTHONPATH=. ./.venv/bin/pytest -q tests --ignore=tests/e2e

api:  ## Run the API locally on :8000
	./.venv/bin/uvicorn app.main:app --reload --port 8000

ui:  ## Run the site locally on :3000
	cd ui && npm install && npm run dev

deploy:  ## Deploy the API to Cloud Run (the site deploys itself on push)
	gcloud run deploy senebiclabs-api --source . --region us-central1 --timeout=3600

health:  ## Check the deployed API
	@curl -s https://api.senebiclabs.com/api/v1/health && echo
