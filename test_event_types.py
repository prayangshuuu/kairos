import datetime
from django.core.exceptions import ValidationError
from apps.accounts.models import User
from apps.scheduling.models import EventType, BookingQuestion

def run_test():
    user = User.objects.first()
    if not user:
        print("No users found!")
        return

    # Create a paid 45-minute event type
    event = EventType.objects.create(
        owner=user,
        slug="paid-consultation",
        title="45 Min Paid Consultation",
        duration_minutes=45,
        price_cents=5000, # $50.00
        currency="USD"
    )
    print(f"Created EventType: {event}")

    # Add a valid custom question
    BookingQuestion.objects.create(
        event_type=event,
        label="What do you want to discuss?",
        field_type="textarea",
        order=1
    )
    print("Added textarea question.")

    # Try to add an invalid select question (only 1 option)
    try:
        invalid_question = BookingQuestion(
            event_type=event,
            label="Pick a topic",
            field_type="select",
            options=["Only One Option"],
            order=2
        )
        invalid_question.clean()
        invalid_question.save()
        print("FAIL: Allowed select question with only 1 option!")
    except ValidationError as e:
        print(f"SUCCESS: Rejected invalid select question. ({e})")
        
    # Test adding a valid select question
    try:
        valid_question = BookingQuestion(
            event_type=event,
            label="Pick a topic",
            field_type="select",
            options=["Topic A", "Topic B"],
            order=2
        )
        valid_question.clean()
        valid_question.save()
        print("Added valid select question.")
    except ValidationError as e:
        print(f"FAIL: Rejected valid select question! ({e})")

if __name__ == "__main__":
    import django
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
    django.setup()
    run_test()
