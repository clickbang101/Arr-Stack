COMPOSE ?= docker compose

.PHONY: up down restart logs pull ps

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f

pull:
	$(COMPOSE) pull

ps:
	$(COMPOSE) ps
