.PHONY: init doctor test build image up down logs backup

init:
	python manage.py init

doctor:
	python manage.py doctor

test:
	python -m unittest discover -s tests -v

build:
	cd web && bun install --frozen-lockfile && bun run build

image:
	docker compose build monitor

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200 monitor

backup:
	python manage.py backup
