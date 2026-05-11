from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="connecteditem",
            name="last_webhook_fingerprint",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]