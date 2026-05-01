COMPOSE  ?= docker compose
ENV_FILE ?= .env.local

# Fall back to .env if .env.local doesn't exist
ifeq (,$(wildcard $(ENV_FILE)))
  ENV_FILE = .env
endif

ENV_ARGS = --env-file $(ENV_FILE)

.PHONY: up down restart logs pull ps extras setup

up:
	$(COMPOSE) $(ENV_ARGS) up -d

down:
	$(COMPOSE) $(ENV_ARGS) down

restart:
	$(COMPOSE) $(ENV_ARGS) restart

logs:
	$(COMPOSE) $(ENV_ARGS) logs -f

pull:
	$(COMPOSE) $(ENV_ARGS) pull

ps:
	$(COMPOSE) $(ENV_ARGS) ps

extras:
	$(COMPOSE) $(ENV_ARGS) --profile extras up -d

setup:
	@bash setup.sh
