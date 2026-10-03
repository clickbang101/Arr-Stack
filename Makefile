COMPOSE ?= docker compose

.PHONY: up down restart logs pull update ps network setup

up: network
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f

pull:
	$(COMPOSE) pull

# Pull new images and recreate only containers whose image changed
update: pull up

ps:
	$(COMPOSE) ps

# arr-net is external so other stacks (Homarr, NPM) can share it
network:
	@docker network inspect arr-net >/dev/null 2>&1 || docker network create arr-net

setup:
	@bash setup.sh
