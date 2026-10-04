# GPU=nvidia adds docker-compose.nvidia.yml (Plex hardware transcoding)
GPU ?=
COMPOSE ?= docker compose -f docker-compose.yml $(if $(GPU),-f docker-compose.$(GPU).yml)

.PHONY: up down restart logs pull update ps network setup gpu-config

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

# Print docker-compose.yml merged with the NVIDIA add-on, ${VARS} kept, for
# pasting into Portainer (which deploys a single file)
gpu-config:
	@docker compose -f docker-compose.yml -f docker-compose.nvidia.yml config --no-interpolate
