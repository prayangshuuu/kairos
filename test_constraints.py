import datetime
from django.db import IntegrityError
from apps.accounts.models import User
from apps.scheduling.models import Schedule, AvailabilityRule, DateOverride

def run_test():
    # 1. Create a user and get the default schedule
    user, _ = User.objects.get_or_create(
        email="test_scheduling@example.com", 
        defaults={"timezone": "America/New_York", "slug": "test-sch"}
    )
    
    # This automatically creates a Mon-Fri 9-5 schedule
    schedule = user.get_default_schedule()
    print(f"Created default schedule for {user.email}: {schedule.name}")
    print(f"Rules count: {schedule.rules.count()}")
    
    # 2. Add one unavailable date
    DateOverride.objects.create(
        schedule=schedule,
        date=datetime.date(2026, 12, 25),
        is_unavailable=True
    )
    print("Added unavailable override for Christmas 2026")
    
    # 3. Add one split-shift override (e.g., 9-12 and 1-5 on a specific day)
    split_date = datetime.date(2026, 11, 27)
    DateOverride.objects.create(
        schedule=schedule,
        date=split_date,
        is_unavailable=False,
        start_time=datetime.time(9, 0),
        end_time=datetime.time(12, 0)
    )
    DateOverride.objects.create(
        schedule=schedule,
        date=split_date,
        is_unavailable=False,
        start_time=datetime.time(13, 0),
        end_time=datetime.time(17, 0)
    )
    print(f"Added split shift overrides for {split_date}")
    
    # 4. Confirm the constraints reject an end_time before start_time in rules
    try:
        AvailabilityRule.objects.create(
            schedule=schedule,
            weekday=0,
            start_time=datetime.time(17, 0),
            end_time=datetime.time(9, 0)  # Invalid!
        )
        print("FAIL: Allowed rule with end_time < start_time!")
    except IntegrityError as e:
        print(f"SUCCESS: Rejected invalid rule. ({e})")
        
    # 5. Confirm the constraints reject an end_time before start_time in overrides
    try:
        DateOverride.objects.create(
            schedule=schedule,
            date=datetime.date(2026, 12, 31),
            is_unavailable=False,
            start_time=datetime.time(20, 0),
            end_time=datetime.time(18, 0)  # Invalid!
        )
        print("FAIL: Allowed override with end_time < start_time!")
    except IntegrityError as e:
        print(f"SUCCESS: Rejected invalid override. ({e})")

if __name__ == "__main__":
    import django
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
    django.setup()
    run_test()
