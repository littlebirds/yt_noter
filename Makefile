DEV_COMPOSE = docker compose -f compose.yaml -f compose.dev.yaml

.PHONY: dev dev-rebuild dev-logs dev-stop

# Reuse the existing inference container/image. Only app changes are watched.
dev:
	$(DEV_COMPOSE) up -d inference
	$(DEV_COMPOSE) up --build --no-deps --watch app

# Manual app-only rebuild, for example after changing environment settings.
dev-rebuild:
	$(DEV_COMPOSE) up --build -d --no-deps app

dev-logs:
	$(DEV_COMPOSE) logs -f app

# Preserve inference and all persistent volumes.
dev-stop:
	$(DEV_COMPOSE) stop app

PYTHON ?= python3
.PHONY: local-setup local local-run local-inference

local-setup:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements.txt

# Native backend, terminal logs, full process-group restart on Python edits.
local:
	$(PYTHON) scripts/local_dev.py

# No watcher: useful with a debugger or when inspecting a single failure.
local-run:
	.venv/bin/python -u -m app.server

# Optional: reuse the embedded model image rather than download it again.
local-inference:
	docker compose -f compose.yaml -f compose.local.yaml up -d --no-deps inference
