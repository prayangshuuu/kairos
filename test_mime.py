import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()

from django.core.mail import EmailMultiAlternatives

msg = EmailMultiAlternatives("Subject", "Body", "from@a.com", ["to@b.com"])
msg.attach_alternative("<html></html>", "text/html")
msg.attach_alternative("BEGIN:VCALENDAR\nEND:VCALENDAR", "text/calendar; method=REQUEST")
print(msg.message().as_string())
