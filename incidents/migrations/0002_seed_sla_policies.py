from datetime import timedelta

from django.db import migrations

POLICIES = {
    "LOW": (timedelta(hours=4), timedelta(hours=24)),
    "MEDIUM": (timedelta(hours=2), timedelta(hours=8)),
    "HIGH": (timedelta(minutes=30), timedelta(hours=2)),
    "CRITICAL": (timedelta(minutes=15), timedelta(minutes=30)),
}


def seed(apps, schema_editor):
    SLAPolicy = apps.get_model("incidents", "SLAPolicy")
    for severity, (response, resolution) in POLICIES.items():
        SLAPolicy.objects.update_or_create(
            severity=severity,
            defaults={"response_time": response, "resolution_time": resolution},
        )


class Migration(migrations.Migration):
    dependencies = [("incidents", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
