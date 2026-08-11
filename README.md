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

## Local Google Calendar Webhooks

Google Calendar Push Notifications require a publicly accessible HTTPS endpoint to receive webhook payloads. For local development, you must expose your local server to the internet using a tunnel like `ngrok`.

### Setting up ngrok

1. **Install ngrok** (if not already installed):
   ```bash
   brew install ngrok/ngrok/ngrok
   ```
2. **Start the tunnel** pointing to your local Django server port (usually 8000):
   ```bash
   ngrok http 8000
   ```
3. **Configure the Webhook URL**: Note the HTTPS URL provided by ngrok (e.g., `https://1234abcd.ngrok-free.app`). Open `config/settings/dev.py` and set `WEBHOOK_BASE_URL` to this value, or export it in your environment:
   ```bash
   export WEBHOOK_BASE_URL="https://1234abcd.ngrok-free.app"
   ```
4. With this configured, when Kairos attempts to register a watch channel with Google (e.g. during an initial connection or renewal), it will use your ngrok tunnel so Google can securely push incremental syncs to your local machine!

## Admin Account

A default superuser has been created for accessing the Django admin interface at `http://127.0.0.1:8000/admin/`.

You can log in with:
- **Email**: `admin@joinkairos.me`
- **Password**: `adminpassword`
