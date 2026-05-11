import io
import json

from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.test import TestCase
from django.urls import reverse

from .models import ConnectedItem, FinancialAccount, Institution, LoanLiability, SyncRun, SyncStatus, SyncTrigger, Transaction
from .services import PlaidConfigurationError, create_link_token, sync_item


class HomePageTests(TestCase):
    def test_homepage_renders_for_authenticated_user(self) -> None:
        user = get_user_model().objects.create_user(username="tester", password="pw")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accounts")
        self.assertContains(response, "Expenses")
        self.assertContains(response, "Forecast")
        self.assertIn("csrftoken", response.cookies)

    def test_homepage_redirects_when_logged_out(self) -> None:
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class FinanceModelTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="chris", password="pw")
        self.institution = Institution.objects.create(
            external_id="ins_123",
            name="Mountain America Credit Union",
        )
        self.item = ConnectedItem.objects.create(
            user=self.user,
            institution=self.institution,
            external_id="item_123",
            access_token="access-sandbox-123",
        )

    def test_can_persist_financial_records(self) -> None:
        account = FinancialAccount.objects.create(
            user=self.user,
            item=self.item,
            institution=self.institution,
            external_id="acct_123",
            name="Everyday Checking",
            official_name="Everyday Checking",
            mask="1234",
            type="depository",
            subtype="checking",
            current_balance=Decimal("2540.11"),
            available_balance=Decimal("2490.11"),
        )
        liability = LoanLiability.objects.create(
            account=account,
            loan_type="auto",
            lender_name="Truist Bank",
            interest_rate_percentage=Decimal("4.99"),
        )
        plaid_transaction = Transaction.objects.create(
            user=self.user,
            item=self.item,
            account=account,
            external_id="txn_123",
            amount=Decimal("42.50"),
            date=date(2026, 5, 8),
            name="Fuel Station",
            merchant_name="Fuel Station",
            category=["Transportation", "Gas"],
        )
        sync_run = SyncRun.objects.create(
            item=self.item,
            trigger=SyncTrigger.INITIAL,
            status=SyncStatus.SUCCEEDED,
            accounts_seen=1,
            transactions_added=1,
        )

        self.assertEqual(str(account), "Everyday Checking • 1234")
        self.assertEqual(liability.account, account)
        self.assertEqual(plaid_transaction.account, account)
        self.assertEqual(sync_run.item, self.item)

    def test_external_ids_are_unique(self) -> None:
        FinancialAccount.objects.create(
            user=self.user,
            item=self.item,
            institution=self.institution,
            external_id="acct_123",
            name="Primary Checking",
            type="depository",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FinancialAccount.objects.create(
                    user=self.user,
                    item=self.item,
                    institution=self.institution,
                    external_id="acct_123",
                    name="Duplicate Checking",
                    type="depository",
                )


class PlaidServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="owner", password="pw")
        self.item = ConnectedItem.objects.create(
            user=self.user,
            external_id="item_abc",
            access_token="access-sandbox-123",
        )

    @override_settings(
        PLAID_CLIENT_ID="client-id",
        PLAID_SECRET="secret",
        PLAID_PRODUCTS=["transactions", "liabilities"],
        PLAID_COUNTRY_CODES=["US"],
        PLAID_CLIENT_NAME="pfapp",
        PLAID_SYNC_ENABLED=True,
    )
    @patch("core.services.build_plaid_client")
    def test_create_link_token_uses_sdk_client(self, build_plaid_client_mock: Mock) -> None:
        client = Mock()
        client.link_token_create.return_value.to_dict.return_value = {"link_token": "link-sandbox-token"}
        build_plaid_client_mock.return_value = client

        token = create_link_token(self.user)

        self.assertEqual(token, "link-sandbox-token")
        client.link_token_create.assert_called_once()

    @override_settings(PLAID_CLIENT_ID="", PLAID_SECRET="")
    def test_missing_credentials_raise_configuration_error(self) -> None:
        with self.assertRaises(PlaidConfigurationError):
            create_link_token(self.user)

    @override_settings(
        PLAID_CLIENT_ID="client-id",
        PLAID_SECRET="secret",
        PLAID_PRODUCTS=["transactions", "liabilities"],
        PLAID_COUNTRY_CODES=["US"],
        PLAID_CLIENT_NAME="pfapp",
        PLAID_SYNC_ENABLED=True,
    )
    @patch("core.services.build_plaid_client")
    def test_sync_item_imports_accounts_transactions_and_liabilities(self, build_plaid_client_mock: Mock) -> None:
        client = Mock()
        client.accounts_get.return_value.to_dict.return_value = {
            "accounts": [
                {
                    "account_id": "acct_123",
                    "name": "Main Checking",
                    "official_name": "Main Checking",
                    "mask": "1234",
                    "type": "depository",
                    "subtype": "checking",
                    "balances": {
                        "current": 2500.5,
                        "available": 2450.5,
                        "limit": None,
                        "iso_currency_code": "USD",
                    },
                }
            ],
            "item": {"institution_id": "ins_123"},
        }
        client.institutions_get_by_id.return_value.to_dict.return_value = {
            "institution": {
                "institution_id": "ins_123",
                "name": "Mountain America Credit Union",
                "url": "https://www.macu.com",
                "primary_color": "#004b87",
                "logo": "logo-data",
            }
        }
        client.liabilities_get.return_value.to_dict.return_value = {
            "liabilities": {
                "student": [],
                "mortgage": [],
                "credit": [],
                "auto": [
                    {
                        "account_id": "acct_123",
                        "interest_rate_percentage": 4.25,
                        "origination_principal_amount": 18000,
                        "last_statement_balance": 12450,
                        "last_payment_amount": 410.2,
                        "minimum_payment_amount": 410.2,
                        "next_payment_due_date": "2026-06-01",
                        "last_payment_date": "2026-05-01",
                        "maturity_date": "2029-05-01",
                        "account_number": "1234",
                        "lender": "Truist Bank",
                    }
                ],
            }
        }
        client.transactions_sync.side_effect = [
            Mock(
                to_dict=Mock(
                    return_value={
                        "added": [
                            {
                                "transaction_id": "txn_123",
                                "account_id": "acct_123",
                                "pending_transaction_id": "",
                                "amount": 42.5,
                                "iso_currency_code": "USD",
                                "date": "2026-05-08",
                                "authorized_date": "2026-05-08",
                                "name": "Fuel Station",
                                "merchant_name": "Fuel Station",
                                "category": ["Transportation", "Gas"],
                                "pending": False,
                            }
                        ],
                        "modified": [],
                        "removed": [],
                        "next_cursor": "cursor-123",
                        "has_more": False,
                    }
                )
            )
        ]
        build_plaid_client_mock.return_value = client

        sync_run = sync_item(item=self.item, trigger=SyncTrigger.MANUAL)

        account = FinancialAccount.objects.get(external_id="acct_123")
        liability = LoanLiability.objects.get(account=account)
        imported_transaction = Transaction.objects.get(external_id="txn_123")
        self.item.refresh_from_db()

        self.assertEqual(sync_run.status, SyncStatus.SUCCEEDED)
        self.assertEqual(sync_run.accounts_seen, 1)
        self.assertEqual(sync_run.transactions_added, 1)
        self.assertEqual(account.name, "Main Checking")
        self.assertEqual(liability.lender_name, "Truist Bank")
        self.assertEqual(imported_transaction.amount, Decimal("42.50"))
        self.assertEqual(self.item.sync_cursor, "cursor-123")


class DashboardViewTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="owner", password="pw")

    def test_authenticated_homepage_returns_vue_shell(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PFApp")
        self.assertContains(response, "Accounts")
        self.assertContains(response, "Forecast")

    @override_settings(
        PLAID_CLIENT_ID="client-id",
        PLAID_SECRET="secret",
        PLAID_PRODUCTS=["transactions", "liabilities"],
        PLAID_COUNTRY_CODES=["US"],
        PLAID_CLIENT_NAME="pfapp",
    )
    @patch("core.views.create_link_token")
    def test_link_token_endpoint_returns_json(self, create_link_token_mock: Mock) -> None:
        create_link_token_mock.return_value = "link-sandbox-token"
        self.client.force_login(self.user)

        response = self.client.post(reverse("plaid-link-token"), content_type="application/json", data="{}")

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"link_token": "link-sandbox-token"})

    @patch("core.views.exchange_public_token")
    @patch("core.views.sync_item")
    def test_exchange_token_endpoint_runs_initial_sync(self, sync_item_mock: Mock, exchange_public_token_mock: Mock) -> None:
        item = ConnectedItem.objects.create(user=self.user, external_id="item_999", access_token="access-token")
        exchange_public_token_mock.return_value = item
        sync_run = SyncRun.objects.create(item=item, trigger=SyncTrigger.INITIAL, status=SyncStatus.SUCCEEDED, transactions_added=3)
        sync_item_mock.return_value = sync_run
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("plaid-exchange-token"),
            content_type="application/json",
            data=json.dumps({"public_token": "public-sandbox-token"}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {"item_id": "item_999", "status": "succeeded", "transactions_added": 3},
        )
        exchange_public_token_mock.assert_called_once_with(user=self.user, public_token="public-sandbox-token")
        sync_item_mock.assert_called_once_with(item=item, trigger=SyncTrigger.INITIAL)

    @patch("core.views.sync_item")
    def test_webhook_endpoint_triggers_sync_for_transactions(self, sync_item_mock: Mock) -> None:
        item = ConnectedItem.objects.create(user=self.user, external_id="item_webhook", access_token="access-token")
        sync_run = SyncRun.objects.create(item=item, trigger=SyncTrigger.WEBHOOK, status=SyncStatus.SUCCEEDED, transactions_added=2)
        sync_item_mock.return_value = sync_run

        response = self.client.post(
            reverse("plaid-webhook"),
            content_type="application/json",
            data=json.dumps({
                "item_id": "item_webhook",
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "SYNC_UPDATES_AVAILABLE",
                "new_transactions": 2,
            }),
        )

        self.assertEqual(response.status_code, 202)
        self.assertJSONEqual(
            response.content,
            {"status": "succeeded", "transactions_added": 2, "transactions_updated": 0},
        )
        sync_item_mock.assert_called_once_with(item=item, trigger=SyncTrigger.WEBHOOK)

    @patch("core.views.sync_item")
    def test_webhook_endpoint_ignores_duplicate_payload(self, sync_item_mock: Mock) -> None:
        item = ConnectedItem.objects.create(user=self.user, external_id="item_duplicate", access_token="access-token")
        sync_item_mock.return_value = Mock(status=SyncStatus.SUCCEEDED, transactions_added=0, transactions_updated=0)
        payload = {
            "item_id": "item_duplicate",
            "webhook_type": "TRANSACTIONS",
            "webhook_code": "DEFAULT_UPDATE",
        }

        first_response = self.client.post(
            reverse("plaid-webhook"),
            content_type="application/json",
            data=json.dumps(payload),
        )
        second_response = self.client.post(
            reverse("plaid-webhook"),
            content_type="application/json",
            data=json.dumps(payload),
        )

        self.assertEqual(first_response.status_code, 202)
        self.assertEqual(second_response.status_code, 202)
        self.assertJSONEqual(second_response.content, {"status": "ignored", "reason": "duplicate"})
        sync_item_mock.assert_called_once()

    def test_webhook_endpoint_rejects_missing_item_id(self) -> None:
        response = self.client.post(
            reverse("plaid-webhook"),
            content_type="application/json",
            data=json.dumps({"webhook_type": "TRANSACTIONS"}),
        )

        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(response.content, {"error": "item_id is required."})


class BootstrapOwnerCommandTests(TestCase):
    def test_bootstrap_owner_creates_staff_user(self) -> None:
        output = io.StringIO()

        call_command(
            "bootstrap_owner",
            username="owner",
            password="secret-pass",
            email="owner@example.com",
            stdout=output,
        )

        user = get_user_model().objects.get(username="owner")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.email, "owner@example.com")
        self.assertTrue(user.check_password("secret-pass"))
        self.assertIn("Created owner account 'owner'.", output.getvalue())

    def test_bootstrap_owner_updates_existing_user(self) -> None:
        user = get_user_model().objects.create_user(username="owner", password="old-pass")
        output = io.StringIO()

        call_command(
            "bootstrap_owner",
            username="owner",
            password="new-pass",
            superuser=True,
            stdout=output,
        )

        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("new-pass"))
        self.assertIn("Updated owner account 'owner'.", output.getvalue())
