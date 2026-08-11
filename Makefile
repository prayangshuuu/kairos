.PHONY: dev tailwind worker beat test lint format migrate selfcheck

dev:
	.venv/bin/python manage.py runserver

tailwind:
	npm run dev

worker:
	.venv/bin/celery -A config worker -l INFO

beat:
	.venv/bin/celery -A config beat -l INFO

test:
	.venv/bin/python manage.py test

lint:
	.venv/bin/flake8 .

format:
	.venv/bin/black .

migrate:
	.venv/bin/python manage.py migrate

selfcheck:
	.venv/bin/python manage.py kairos_selfcheck
