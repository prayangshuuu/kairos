from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Self checks the Kairos deployment health."

    def handle(self, *args, **options):
        self.stdout.write("Starting Kairos Selfcheck...")

        # 1. Check btree_gist
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist';")
                res = cursor.fetchone()
                if res:
                    self.stdout.write(self.style.SUCCESS("✓ btree_gist is installed"))
                else:
                    self.stdout.write(self.style.ERROR("✗ btree_gist is NOT installed"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Database query failed: {e}"))

        # 2. Check exclusion constraint on bookings
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM pg_constraint WHERE conname = 'no_overlapping_bookings_per_host';"
                )
                if cursor.fetchone():
                    self.stdout.write(self.style.SUCCESS("✓ Exclusion constraint exists"))
                else:
                    self.stdout.write(self.style.ERROR("✗ Exclusion constraint NOT found"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Database query failed: {e}"))

        # 3. Check Celery Broker
        try:
            from config.celery import app as celery_app

            with celery_app.connection_or_acquire() as conn:
                conn.default_channel.basic_qos(0, 1, False)
            self.stdout.write(self.style.SUCCESS("✓ Celery broker reachable"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Celery broker unreachable: {e}"))

        # 4. Check Email configuration
        if getattr(settings, "EMAIL_HOST", None):
            self.stdout.write(
                self.style.SUCCESS(f"✓ Email backend configured ({settings.EMAIL_HOST})")
            )
        else:
            self.stdout.write(
                self.style.WARNING("! Email backend NOT configured (using console/dummy?)")
            )

        # 5. Check Default Schedules
        users_without_schedule = User.objects.filter(schedules__isnull=True).count()
        if users_without_schedule == 0:
            self.stdout.write(self.style.SUCCESS("✓ All users have schedules"))
        else:
            self.stdout.write(
                self.style.WARNING(f"! {users_without_schedule} user(s) lack a schedule")
            )

        self.stdout.write("Selfcheck complete.")
