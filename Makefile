# Fleetdeck: local demo on k3d. Run `make help` for the list of targets.
SHELL := /bin/bash
CLUSTER ?= fleetdeck
NAMESPACE ?= fleet-system
PLATFORM_IMAGE ?= fleetdeck/platform:dev
HERMES_IMAGE ?= nousresearch/hermes-agent:stable
export KUBECONFIG := $(CURDIR)/.dev-kubeconfig

.DEFAULT_GOAL := help
.PHONY: help cluster images up deploy down status lint typecheck test test-cluster test-live

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

cluster: ## Create the k3d cluster and load the Hermes image into it
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || k3d cluster create --config deploy/k3d/cluster.yaml
	@k3d kubeconfig get $(CLUSTER) > $(KUBECONFIG) && chmod 600 $(KUBECONFIG)
	@docker image inspect $(HERMES_IMAGE) >/dev/null 2>&1 || docker pull $(HERMES_IMAGE)
	@docker exec k3d-$(CLUSTER)-server-0 crictl inspecti $(HERMES_IMAGE) >/dev/null 2>&1 \
		|| k3d image import $(HERMES_IMAGE) -c $(CLUSTER)
	@kubectl wait --for=condition=Ready node --all --timeout=120s

images: ## Build the platform image and load it into the cluster
	docker build -q -t $(PLATFORM_IMAGE) .
	k3d image import $(PLATFORM_IMAGE) -c $(CLUSTER)

up: cluster images deploy ## Create the cluster, build, and install the platform with the demo agents

deploy: ## Install or upgrade the release (reads the model key from .env)
	@test -f .env || { echo "copy .env.example to .env and set OPENROUTER_API_KEY"; exit 1; }
	@set -a; . ./.env; set +a; \
	helm upgrade --install fleetdeck deploy/chart --namespace $(NAMESPACE) --create-namespace \
		--set secrets.openrouterApiKey="$$OPENROUTER_API_KEY" \
		--set models.default="$$LLM_MODEL" --set models.restricted="$$RESTRICTED_MODEL" \
		--set image.buildId="$$(docker image inspect --format '{{.Id}}' $(PLATFORM_IMAGE))" \
		--wait --timeout 10m
	@$(MAKE) --no-print-directory status

status: ## Show the platform pods and the agents
	@kubectl -n $(NAMESPACE) get pods
	@kubectl get agents.fleetdeck.dev

down: ## Delete the cluster
	k3d cluster delete $(CLUSTER)
	@rm -f $(KUBECONFIG)

lint: ## Ruff and Helm lint
	uv run ruff check .
	uv run ruff format --check .
	helm lint deploy/chart

typecheck: ## mypy --strict
	uv run mypy common/src control-plane/src operator/src model-gateway/src egress-proxy/src mock-systems/src connectors/src

test: ## Unit and API tests (need Docker for a throwaway PostgreSQL, no cluster, no model key)
	uv run pytest

test-cluster: ## Isolation and scale-to-zero tests against the running k3d cluster (no model calls)
	uv run pytest -m "cluster and not live" tests/cluster

test-live: ## Cluster tests that call a real model through OpenRouter (costs a fraction of a cent)
	uv run pytest -m live tests/cluster
