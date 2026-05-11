from django.urls import path

from .views import (
    api_accounts,
    api_expense_detail,
    api_expenses,
    api_income,
    api_income_detail,
    api_transactions,
    home,
    plaid_exchange_token,
    plaid_link_token,
    plaid_webhook,
    refresh_item,
)

urlpatterns = [
    path("", home, name="home"),
    # Plaid
    path("plaid/link-token/", plaid_link_token, name="plaid-link-token"),
    path("plaid/exchange-token/", plaid_exchange_token, name="plaid-exchange-token"),
    path("plaid/webhook/", plaid_webhook, name="plaid-webhook"),
    path("items/<int:item_id>/refresh/", refresh_item, name="refresh-item"),
    # API
    path("api/accounts/", api_accounts, name="api-accounts"),
    path("api/transactions/", api_transactions, name="api-transactions"),
    path("api/expenses/", api_expenses, name="api-expenses"),
    path("api/expenses/<int:expense_id>/", api_expense_detail, name="api-expense-detail"),
    path("api/income/", api_income, name="api-income"),
    path("api/income/<int:source_id>/", api_income_detail, name="api-income-detail"),
]
