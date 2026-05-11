from django.contrib import admin

from .models import ConnectedItem, Expense, FinancialAccount, IncomeSource, Institution, LoanLiability, SyncRun, Transaction


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "external_id", "updated_at")
    search_fields = ("name", "external_id")


@admin.register(ConnectedItem)
class ConnectedItemAdmin(admin.ModelAdmin):
    list_display = ("external_id", "user", "provider", "status", "institution", "last_successful_sync_at")
    list_filter = ("provider", "status")
    search_fields = ("external_id", "user__username", "institution__name")


@admin.register(FinancialAccount)
class FinancialAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "type", "subtype", "current_balance", "is_active")
    list_filter = ("type", "subtype", "is_active")
    search_fields = ("name", "official_name", "external_id", "mask")


@admin.register(LoanLiability)
class LoanLiabilityAdmin(admin.ModelAdmin):
    list_display = ("account", "loan_type", "lender_name", "interest_rate_percentage", "next_payment_due_date")
    search_fields = ("account__name", "lender_name")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "account", "amount", "pending")
    list_filter = ("pending", "iso_currency_code")
    search_fields = ("name", "merchant_name", "external_id")


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = ("item", "trigger", "status", "accounts_seen", "transactions_added", "started_at")
    list_filter = ("trigger", "status")
    search_fields = ("item__external_id",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "category", "frequency", "amount", "is_active")
    list_filter = ("category", "frequency", "is_active")
    search_fields = ("name", "user__username")


@admin.register(IncomeSource)
class IncomeSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "category", "frequency", "amount", "is_active")
    list_filter = ("category", "frequency", "is_active")
    search_fields = ("name", "user__username")