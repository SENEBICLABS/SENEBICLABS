# Common tasks. `make` on its own lists them.

.DEFAULT_GOAL := help
.PHONY: help setup test test-e2e api ui ls ls-stop ls-reset ls-token deploy health

help:  ## Show this list
	@grep -E '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## Create the venv, install dependencies, copy the env template
	python3.10 -m venv .venv
	./.venv/bin/pip install --upgrade pip
	./.venv/bin/pip install -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "Done. Activate with: source .venv/bin/activate"

test:  ## Run the hermetic suite (no database, no network, no Label Studio)
	PYTHONPATH=. ./.venv/bin/pytest -q tests --ignore=tests/e2e

test-e2e:  ## Run the end-to-end test (needs: make ls, the API on :8000, workforce on :3000)
	PYTHONPATH=. ./.venv/bin/pytest -q tests/e2e

api:  ## Run the API locally on :8000
	./.venv/bin/uvicorn app.main:app --reload --port 8000

ui:  ## Run the site locally on :3000
	cd ui && npm install && npm run dev

ls:  ## Start Label Studio locally on :8080 (free; no VM, no DNS, no certificate)
	cd docker/labelstudio && docker compose up -d
	@echo "Label Studio starting on http://localhost:8080 (first boot builds its schema)"
	@echo "Point the API at it:"
	@echo "  LS_URL=http://localhost:8080"
	@echo "  LS_CALLBACK_URL=http://host.docker.internal:8000/api/v1/ls/webhook"
	@echo "Then: make ls-token"

ls-stop:  ## Stop local Label Studio, keeping its data
	cd docker/labelstudio && docker compose stop

ls-reset:  ## Stop local Label Studio and delete its data
	cd docker/labelstudio && docker compose down -v

ls-token:  ## Print the access token to use as LS_TOKEN
	@echo "Open http://localhost:8080 -> Account and Settings -> Access Token"
	@echo "Put it in .env as LS_TOKEN, then restart the API."

deploy:  ## Deploy the API (Render builds from render.yaml on push to main)
	@echo "The API deploys itself: push to main and Render builds render.yaml."
	@echo "First time: create a Blueprint on Render pointed at this repository."
	@echo "The site deploys itself on push too, via Vercel."

health:  ## Check the deployed API
	@curl -s https://api.senebiclabs.com/api/v1/health && echo
