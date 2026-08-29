from decimal import Decimal

from django.db import migrations


def seed_default_plans(apps, schema_editor):
    plan_model = apps.get_model("billing", "SubscriptionPlan")
    defaults = [
        {
            "code": "FREE",
            "name": "Free",
            "description": "Free plan for trying private document chat.",
            "price": Decimal("0.00"),
            "currency": "VND",
            "duration_days": 30,
            "daily_token_limit": 5000,
            "max_documents": 3,
            "max_file_size_mb": 5,
            "is_active": True,
        },
        {
            "code": "PRO",
            "name": "Pro",
            "description": "Paid plan with higher document and token limits.",
            "price": Decimal("99000.00"),
            "currency": "VND",
            "duration_days": 30,
            "daily_token_limit": 50000,
            "max_documents": 100,
            "max_file_size_mb": 20,
            "is_active": True,
        },
    ]
    for values in defaults:
        code = values.pop("code")
        plan_model.objects.update_or_create(code=code, defaults=values)


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]

    operations = [migrations.RunPython(seed_default_plans, migrations.RunPython.noop)]
