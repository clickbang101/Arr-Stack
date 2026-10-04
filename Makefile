# Add-ons: ADDONS="nvidia vpn" adds docker-compose.nvidia.yml / docker-compose.vpn.yml.
# GPU=nvidia is kept as a shortcut for ADDONS=nvidia.
GPU ?=
ADDONS ?= $(GPU)
COMPOSE ?= docker compose -f docker-compose.yml $(foreach a,$(ADDONS),-f docker-compose.$(a).yml)

.PHONY: up down restart logs pull update ps network setup gpu-config portainer-config backup bot-install

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

# Consistent, compressed backup of APPDATA_PATH (safe while running)
backup:
	@set -a; . ./.env; set +a; mkdir -p backups; \
	  scripts/backup-appdata.sh > backups/appdata-$$(date +%F).tar.zst && ls -lh backups | tail -1

# One merged compose file (main + ADDONS) for pasting into Portainer, which
# deploys a single file. ${VARS} are kept for Portainer's environment variables.
portainer-config:
	@python3 scripts/portainer-config.py $(ADDONS)

gpu-config:
	@python3 scripts/portainer-config.py nvidia

# Copy the Telegram bot script into APPDATA_PATH (the arrbot container runs it from there)
bot-install:
	@set -a; . ./.env; set +a; install -D -m 644 bot/arrbot.py "$$APPDATA_PATH/arrbot/arrbot.py" && echo "installed to $$APPDATA_PATH/arrbot/"
