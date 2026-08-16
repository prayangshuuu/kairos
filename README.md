# Kairos

Kairos is a 100% free scheduling platform with no subscription tiers or artificial feature limits. Every feature (unlimited event types, schedules, workflows, custom questions, client CRM/records, and bookings) is available to all users.

**Revenue Model:**
- **0% service charge** for hosts using their own Stripe Connect account.
- **3% service charge** for hosts accepting paid bookings via Kairos PayStation.

## Quick Start

1. **Install dependencies:** `uv sync`
2. **Start backing services (PostgreSQL & Redis):** `docker compose up -d`
3. **Run migrations:** `uv run python manage.py migrate`
4. **Build Tailwind CSS:** 
   - Once: `./tailwindcss -i ./static/src/input.css -o ./static/css/output.css`
   - Watch: `./tailwindcss -i ./static/src/input.css -o ./static/css/output.css --watch`
5. **Start server:** `uv run python manage.py runserver`

## Accounts

**Demo User** (Login at `/accounts/login/`):
Create one via Django shell (`uv run python manage.py shell`):
```python
from apps.accounts.models import User
User.objects.create_user(email="test@joinkairos.tech", password="password")
```
*Use `test@joinkairos.tech` / `password` to log in.*

**Admin User** (Login at `/admin/`):
- **Email:** `admin@joinkairos.tech`
- **Password:** `adminpassword`

## Integrations

### Local Google Calendar Webhooks
Google Calendar push notifications require a public HTTPS endpoint. Use `ngrok` for local development:
1. `brew install ngrok/ngrok/ngrok`
2. `ngrok http 8000`
3. Set `WEBHOOK_BASE_URL="https://<your-ngrok-id>.ngrok-free.app"` in your `.env`.

### Zoom
*Note: The Zoom app is currently under review by Zoom. Use in development mode only.*

---
*Copyright 2026 [Prayangshu Biswas Hritwick](https://prayangshu.com) & [Dwimik Software](https://www.dwimiksoftware.com)*
