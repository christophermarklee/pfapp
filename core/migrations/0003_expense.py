from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_connecteditem_last_webhook_fingerprint"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Expense",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("housing", "Housing"),
                            ("utilities", "Utilities"),
                            ("food", "Food & Groceries"),
                            ("transport", "Transportation"),
                            ("gas", "Gas"),
                            ("insurance", "Insurance"),
                            ("subscriptions", "Subscriptions"),
                            ("debt", "Debt Payments"),
                            ("medical", "Medical"),
                            ("other", "Other"),
                        ],
                        default="other",
                        max_length=64,
                    ),
                ),
                (
                    "frequency",
                    models.CharField(
                        choices=[
                            ("one_time", "One-time"),
                            ("weekly", "Weekly"),
                            ("biweekly", "Bi-weekly"),
                            ("monthly", "Monthly"),
                            ("quarterly", "Quarterly"),
                            ("annual", "Annual"),
                        ],
                        default="monthly",
                        max_length=32,
                    ),
                ),
                (
                    "due_day",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        help_text="Day of month (1–31) for recurring expenses",
                        null=True,
                    ),
                ),
                (
                    "due_date",
                    models.DateField(
                        blank=True,
                        help_text="Due date for one-time expenses",
                        null=True,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="expenses",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
    ]
