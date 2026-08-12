import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from apps.payments.providers import PayStationProvider

class MockPayment:
    uid = "test1234"
    invoice_number = "INV-1234"
    amount_cents = 10000
    slot = None

provider = PayStationProvider()
print(provider.client.base_url)
try:
    res = provider.create_checkout(MockPayment(), "http://127.0.0.1:8000/success", "http://127.0.0.1:8000/cancel")
    print(res.payment_url)
except Exception as e:
    print(e)
