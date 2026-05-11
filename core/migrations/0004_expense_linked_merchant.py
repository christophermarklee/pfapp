from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_expense"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="linked_merchant",
            field=models.CharField(
                blank=True,
                max_length=255,
                help_text="Merchant name pattern to auto-match against imported transactions",
            ),
        ),
    ]
