.PHONY: help up down logs build clean

.DEFAULT_GOAL := help

help: ## Show commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Start arb bot
	docker compose up -d

up-build: ## Build and start
	docker compose up -d --build

down: ## Stop
	docker compose down

logs: ## Tail logs
	docker compose logs -f arber

shell: ## Shell into container
	docker compose exec arber bash

build: ## Build image
	docker compose build

clean: ## Remove everything
	docker compose down -v --rmi local

dev: ## Run locally (no Docker)
	cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8020 --reload
