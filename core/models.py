from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class Provider(models.TextChoices):
    PLAID = "plaid", "Plaid"


class ItemStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ERROR = "error", "Error"
    REQUIRES_ATTENTION = "requires_attention", "Requires attention"
    REMOVED = "removed", "Removed"


class SyncTrigger(models.TextChoices):
    INITIAL = "initial", "Initial"
    MANUAL = "manual", "Manual"
    SCHEDULED = "scheduled", "Scheduled"
    WEBHOOK = "webhook", "Webhook"


class SyncStatus(models.TextChoices):
    STARTED = "started", "Started"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class Institution(models.Model):
    provider = models.CharField(max_length=32, choices=Provider.choices, default=Provider.PLAID)
    external_id = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True)
    primary_color = models.CharField(max_length=7, blank=True)
    logo = models.TextField(blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ConnectedItem(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="connected_items")
    institution = models.ForeignKey(
        Institution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connected_items",
    )
    provider = models.CharField(max_length=32, choices=Provider.choices, default=Provider.PLAID)
    external_id = models.CharField(max_length=255, unique=True)
    access_token = models.CharField(max_length=255)
    status = models.CharField(max_length=32, choices=ItemStatus.choices, default=ItemStatus.ACTIVE)
    sync_cursor = models.TextField(blank=True)
    last_successful_sync_at = models.DateTimeField(null=True, blank=True)
    last_webhook_code = models.CharField(max_length=128, blank=True)
    last_webhook_fingerprint = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.get_provider_display()} item {self.external_id}"


class FinancialAccount(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="financial_accounts")
    item = models.ForeignKey(ConnectedItem, on_delete=models.CASCADE, related_name="accounts")
    institution = models.ForeignKey(
        Institution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accounts",
    )
    external_id = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    official_name = models.CharField(max_length=255, blank=True)
    mask = models.CharField(max_length=8, blank=True)
    type = models.CharField(max_length=64)
    subtype = models.CharField(max_length=64, blank=True)
    currency_code = models.CharField(max_length=3, default="USD")
    current_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    available_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        suffix = f" • {self.mask}" if self.mask else ""
        return f"{self.name}{suffix}"


class LoanLiability(models.Model):
    account = models.OneToOneField(FinancialAccount, on_delete=models.CASCADE, related_name="loan_liability")
    provider = models.CharField(max_length=32, choices=Provider.choices, default=Provider.PLAID)
    loan_type = models.CharField(max_length=64, blank=True)
    lender_name = models.CharField(max_length=255, blank=True)
    account_number_mask = models.CharField(max_length=8, blank=True)
    interest_rate_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    originating_principal_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    last_statement_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    last_payment_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    minimum_payment_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    next_payment_due_date = models.DateField(null=True, blank=True)
    last_payment_date = models.DateField(null=True, blank=True)
    maturity_date = models.DateField(null=True, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Loan details for {self.account}"


class Transaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")
    item = models.ForeignKey(ConnectedItem, on_delete=models.CASCADE, related_name="transactions")
    account = models.ForeignKey(FinancialAccount, on_delete=models.CASCADE, related_name="transactions")
    external_id = models.CharField(max_length=255, unique=True)
    pending_transaction_id = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    iso_currency_code = models.CharField(max_length=3, default="USD")
    date = models.DateField()
    authorized_date = models.DateField(null=True, blank=True)
    name = models.CharField(max_length=255)
    merchant_name = models.CharField(max_length=255, blank=True)
    category = models.JSONField(default=list, blank=True)
    pending = models.BooleanField(default=False)
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "name"]

    def __str__(self) -> str:
        return self.name


class ExpenseFrequency(models.TextChoices):
    ONE_TIME = "one_time", "One-time"
    WEEKLY = "weekly", "Weekly"
    BIWEEKLY = "biweekly", "Bi-weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    ANNUAL = "annual", "Annual"


class ExpenseCategory(models.TextChoices):
    HOUSING = "housing", "Housing"
    UTILITIES = "utilities", "Utilities"
    FOOD = "food", "Food & Groceries"
    TRANSPORT = "transport", "Transportation"
    GAS = "gas", "Gas"
    INSURANCE = "insurance", "Insurance"
    SUBSCRIPTIONS = "subscriptions", "Subscriptions"
    DEBT = "debt", "Debt Payments"
    MEDICAL = "medical", "Medical"
    OTHER = "other", "Other"


class Expense(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="expenses",
    )
    name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.CharField(max_length=64, choices=ExpenseCategory.choices, default=ExpenseCategory.OTHER)
    frequency = models.CharField(max_length=32, choices=ExpenseFrequency.choices, default=ExpenseFrequency.MONTHLY)
    due_day = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Day of month (1–31) for recurring expenses",
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Due date for one-time expenses",
    )
    notes = models.TextField(blank=True)
    linked_merchant = models.CharField(
        max_length=255,
        blank=True,
        help_text="Merchant name pattern to auto-match against imported transactions",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_frequency_display()})"

    @property
    def monthly_amount(self) -> Decimal:
        """Monthly equivalent for forecasting (one-time expenses return 0)."""
        multipliers: dict[str, Decimal] = {
            ExpenseFrequency.WEEKLY: Decimal("4.33"),
            ExpenseFrequency.BIWEEKLY: Decimal("2.17"),
            ExpenseFrequency.MONTHLY: Decimal("1"),
            ExpenseFrequency.QUARTERLY: Decimal("0.3333"),
            ExpenseFrequency.ANNUAL: Decimal("0.0833"),
            ExpenseFrequency.ONE_TIME: Decimal("0"),
        }
        return (self.amount * multipliers.get(self.frequency, Decimal("1"))).quantize(Decimal("0.01"))


class IncomeFrequency(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    BIWEEKLY = "biweekly", "Bi-weekly"
    SEMIMONTHLY = "semimonthly", "Semi-monthly (1st & 15th)"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    ANNUAL = "annual", "Annual"
    ONE_TIME = "one_time", "One-time"


class IncomeCategory(models.TextChoices):
    SALARY = "salary", "Salary"
    BONUS = "bonus", "Bonus"
    FREELANCE = "freelance", "Freelance / Contract"
    INVESTMENT = "investment", "Investment / Dividends"
    RENTAL = "rental", "Rental Income"
    OTHER = "other", "Other"


class IncomeSource(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="income_sources",
    )
    name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="Amount per payment")
    frequency = models.CharField(max_length=32, choices=IncomeFrequency.choices, default=IncomeFrequency.MONTHLY)
    category = models.CharField(max_length=32, choices=IncomeCategory.choices, default=IncomeCategory.SALARY)
    next_pay_date = models.DateField(
        null=True,
        blank=True,
        help_text="Anchor date for bi-weekly/weekly; expected date for quarterly/annual/one-time",
    )
    day_of_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Day of month (1–28) for monthly income",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-amount", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_frequency_display()})"

    @property
    def monthly_amount(self) -> Decimal:
        multipliers: dict[str, Decimal] = {
            IncomeFrequency.WEEKLY: Decimal("4.3333"),
            IncomeFrequency.BIWEEKLY: Decimal("2.1667"),
            IncomeFrequency.SEMIMONTHLY: Decimal("2"),
            IncomeFrequency.MONTHLY: Decimal("1"),
            IncomeFrequency.QUARTERLY: Decimal("0.3333"),
            IncomeFrequency.ANNUAL: Decimal("0.0833"),
            IncomeFrequency.ONE_TIME: Decimal("0"),
        }
        return (self.amount * multipliers.get(self.frequency, Decimal("1"))).quantize(Decimal("0.01"))

    @property
    def annual_amount(self) -> Decimal:
        multipliers: dict[str, Decimal] = {
            IncomeFrequency.WEEKLY: Decimal("52"),
            IncomeFrequency.BIWEEKLY: Decimal("26"),
            IncomeFrequency.SEMIMONTHLY: Decimal("24"),
            IncomeFrequency.MONTHLY: Decimal("12"),
            IncomeFrequency.QUARTERLY: Decimal("4"),
            IncomeFrequency.ANNUAL: Decimal("1"),
            IncomeFrequency.ONE_TIME: Decimal("1"),
        }
        return (self.amount * multipliers.get(self.frequency, Decimal("1"))).quantize(Decimal("0.01"))


class SyncRun(models.Model):
    item = models.ForeignKey(ConnectedItem, on_delete=models.CASCADE, related_name="sync_runs")
    trigger = models.CharField(max_length=32, choices=SyncTrigger.choices)
    status = models.CharField(max_length=32, choices=SyncStatus.choices, default=SyncStatus.STARTED)
    accounts_seen = models.PositiveIntegerField(default=0)
    transactions_added = models.PositiveIntegerField(default=0)
    transactions_updated = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.get_trigger_display()} sync for {self.item.external_id}"