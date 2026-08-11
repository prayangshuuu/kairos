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

## Demo Account

To create a quick test user for testing the dashboard and onboarding flows, run the following command in the Django shell (`uv run python manage.py shell`):

```python
from apps.accounts.models import User
user = User.objects.create_user(email="test@joinkairos.me", password="password")
```

You can then log in at `http://127.0.0.1:8000/accounts/login/` with:
- **Email**: `test@joinkairos.me`
- **Password**: `password`

## Admin Account

A default superuser has been created for accessing the Django admin interface at `http://127.0.0.1:8000/admin/`.

You can log in with:
- **Email**: `admin@joinkairos.me`
- **Password**: `adminpassword`
