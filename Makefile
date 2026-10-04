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

# Print docker-compose.yml with the NVIDIA add-on inserted into the plex service,
# ${VARS} kept, for pasting into Portainer (which deploys a single file).
# Text insert on purpose: `docker compose config --no-interpolate` turns bind
# mounts into named volumes.
gpu-config:
	@awk '/^  plex:/{p=1} {print} \
	  p && /^    cpus:/{print "    deploy:\n      resources:\n        reservations:\n          devices:\n            - driver: nvidia\n              count: all\n              capabilities: [gpu]"} \
	  p && /- VERSION=docker/{print "      - NVIDIA_VISIBLE_DEVICES=all\n      - NVIDIA_DRIVER_CAPABILITIES=compute,video,utility"; p=0}' docker-compose.yml
