from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0004_walletreconciliationlog_hostledger_created_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostledger",
            name="provider",
            field=models.CharField(
                choices=[("paystation", "PayStation"), ("stripe", "Stripe Connect")],
                db_index=True,
                default="paystation",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="hostledger",
            name="is_custodial",
            field=models.BooleanField(
                default=True,
                db_index=True,
                help_text=(
                    "True for PayStation entries (Kairos holds the funds). "
                    "False for Stripe entries (informational only — never sum into a balance)."
                ),
            ),
        ),
    ]
