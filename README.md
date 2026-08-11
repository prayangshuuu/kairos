# Kairos

Scheduling and paid-appointments platform.

## Setup

1. Install dependencies:
   ```bash
   uv sync
   ```
2. Start services (PostgreSQL and Redis):
   ```bash
   docker compose up -d
   ```
3. Run migrations:
   ```bash
   uv run python manage.py migrate
   ```
4. Build Tailwind CSS:
   ```bash
   # Build once
   ./tailwindcss -i ./static/src/input.css -o ./static/css/output.css
   
   # Or watch for changes during development
   ./tailwindcss -i ./static/src/input.css -o ./static/css/output.css --watch
   ```
5. Start development server:
   ```bash
   uv run python manage.py runserver
   ```
