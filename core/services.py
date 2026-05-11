from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from plaid.api import plaid_api
from plaid.api_client import ApiClient
from plaid.configuration import Configuration
from plaid.exceptions import ApiException
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.liabilities_get_request import LiabilitiesGetRequest
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from .models import ConnectedItem, FinancialAccount, Institution, ItemStatus, LoanLiability, SyncRun, SyncStatus, SyncTrigger, Transaction


class PlaidConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlaidSettings:
    client_id: str
    secret: str
    env: str
    products: list[str]
    country_codes: list[str]
    redirect_uri: str
    webhook_url: str
    client_name: str
    sync_enabled: bool


def _plaid_settings() -> PlaidSettings:
    return PlaidSettings(
        client_id=settings.PLAID_CLIENT_ID,
        secret=settings.PLAID_SECRET,
        env=settings.PLAID_ENV,
        products=settings.PLAID_PRODUCTS,
        country_codes=settings.PLAID_COUNTRY_CODES,
        redirect_uri=settings.PLAID_REDIRECT_URI,
        webhook_url=settings.PLAID_WEBHOOK_URL,
        client_name=settings.PLAID_CLIENT_NAME,
        sync_enabled=settings.PLAID_SYNC_ENABLED,
    )


def _require_credentials(config: PlaidSettings) -> None:
    if not config.client_id or not config.secret:
        raise PlaidConfigurationError("PLAID_CLIENT_ID and PLAID_SECRET must be configured before using Plaid.")


def _enum_values(enum_cls: type[Any], values: list[str]) -> list[Any]:
    raw_values = getattr(enum_cls, "allowed_values", {}).get(("value",), {})
    enum_map = {str(value).lower(): str(value) for value in raw_values.values()}
    resolved: list[Any] = []
    for raw_value in values:
        key = raw_value.lower()
        if key not in enum_map:
            allowed = ", ".join(sorted(enum_map))
            raise PlaidConfigurationError(f"Unsupported Plaid value '{raw_value}'. Allowed values: {allowed}")
        resolved.append(enum_cls(enum_map[key]))
    return resolved


def build_plaid_client() -> plaid_api.PlaidApi:
    config = _plaid_settings()
    _require_credentials(config)
    host = Configuration(host=f"https://{config.env}.plaid.com", api_key={"clientId": config.client_id, "secret": config.secret})
    return plaid_api.PlaidApi(ApiClient(host))


def create_link_token(user: Any) -> str:
    config = _plaid_settings()
    client = build_plaid_client()
    request = LinkTokenCreateRequest(
        client_name=config.client_name,
        language="en",
        country_codes=_enum_values(CountryCode, config.country_codes),
        user=LinkTokenCreateRequestUser(client_user_id=str(user.pk)),
        products=_enum_values(Products, config.products),
    )

    if config.redirect_uri:
        request.redirect_uri = config.redirect_uri
    if config.webhook_url:
        request.webhook = config.webhook_url

    return client.link_token_create(request).to_dict()["link_token"]


def exchange_public_token(*, user: Any, public_token: str) -> ConnectedItem:
    client = build_plaid_client()
    response = client.item_public_token_exchange(ItemPublicTokenExchangeRequest(public_token=public_token)).to_dict()
    item, _ = ConnectedItem.objects.update_or_create(
        user=user,
        external_id=response["item_id"],
        defaults={
            "access_token": response["access_token"],
            "provider": "plaid",
            "status": ItemStatus.ACTIVE,
            "error_message": "",
            "raw_data": _json_ready(response),
        },
    )
    return item


def sync_all_items(*, user: Any | None = None, trigger: SyncTrigger = SyncTrigger.SCHEDULED) -> list[SyncRun]:
    items = ConnectedItem.objects.all().select_related("institution", "user")
    if user is not None:
        items = items.filter(user=user)
    return [sync_item(item=item, trigger=trigger) for item in items]


def sync_item(*, item: ConnectedItem, trigger: SyncTrigger = SyncTrigger.MANUAL) -> SyncRun:
    config = _plaid_settings()
    sync_run = SyncRun.objects.create(item=item, trigger=trigger, status=SyncStatus.STARTED)

    if not config.sync_enabled:
        sync_run.status = SyncStatus.FAILED
        sync_run.error_message = "Plaid sync is disabled by settings."
        sync_run.finished_at = timezone.now()
        sync_run.save(update_fields=["status", "error_message", "finished_at"])
        return sync_run

    try:
        client = build_plaid_client()
        with transaction.atomic():
            accounts_payload = client.accounts_get(AccountsGetRequest(access_token=item.access_token)).to_dict()
            institution = _upsert_institution(client=client, accounts_payload=accounts_payload)
            if institution is not None and item.institution_id != institution.id:
                item.institution = institution
            item.raw_data = {**_json_ready(item.raw_data), "accounts_get": _json_ready(accounts_payload)}
            item.error_message = ""
            item.status = ItemStatus.ACTIVE
            accounts_seen = _sync_accounts(item=item, accounts_payload=accounts_payload)
            liabilities_payload = _fetch_liabilities(client=client, access_token=item.access_token)
            if liabilities_payload is not None:
                item.raw_data["liabilities_get"] = _json_ready(liabilities_payload)
                _sync_liabilities(item=item, liabilities_payload=liabilities_payload)
            transactions_added, transactions_updated, next_cursor = _sync_transactions(client=client, item=item)
            item.sync_cursor = next_cursor
            item.last_successful_sync_at = timezone.now()
            item.save(
                update_fields=[
                    "institution",
                    "raw_data",
                    "error_message",
                    "status",
                    "sync_cursor",
                    "last_successful_sync_at",
                    "updated_at",
                ]
            )

        sync_run.status = SyncStatus.SUCCEEDED
        sync_run.accounts_seen = accounts_seen
        sync_run.transactions_added = transactions_added
        sync_run.transactions_updated = transactions_updated
        sync_run.finished_at = timezone.now()
        sync_run.save(update_fields=["status", "accounts_seen", "transactions_added", "transactions_updated", "finished_at"])
        return sync_run
    except ApiException as exc:
        message = _extract_api_error(exc)
    except PlaidConfigurationError as exc:
        message = str(exc)

    item.status = ItemStatus.ERROR
    item.error_message = message
    item.save(update_fields=["status", "error_message", "updated_at"])
    sync_run.status = SyncStatus.FAILED
    sync_run.error_message = message
    sync_run.finished_at = timezone.now()
    sync_run.save(update_fields=["status", "error_message", "finished_at"])
    return sync_run


def _upsert_institution(*, client: plaid_api.PlaidApi, accounts_payload: dict[str, Any]) -> Institution | None:
    item_data = accounts_payload.get("item") or {}
    institution_id = item_data.get("institution_id")
    if not institution_id:
        return None

    request = InstitutionsGetByIdRequest(
        institution_id=institution_id,
        country_codes=_enum_values(CountryCode, _plaid_settings().country_codes),
    )
    institution_payload = client.institutions_get_by_id(request).to_dict()["institution"]
    institution, _ = Institution.objects.update_or_create(
        external_id=institution_payload["institution_id"],
        defaults={
            "provider": "plaid",
            "name": institution_payload["name"],
            "url": institution_payload.get("url") or "",
            "primary_color": institution_payload.get("primary_color") or "",
            "logo": institution_payload.get("logo") or "",
            "raw_data": _json_ready(institution_payload),
        },
    )
    return institution


def _sync_accounts(*, item: ConnectedItem, accounts_payload: dict[str, Any]) -> int:
    seen_ids: set[str] = set()
    for account_payload in accounts_payload.get("accounts", []):
        seen_ids.add(account_payload["account_id"])
        balances = account_payload.get("balances") or {}
        FinancialAccount.objects.update_or_create(
            external_id=account_payload["account_id"],
            defaults={
                "user": item.user,
                "item": item,
                "institution": item.institution,
                "name": account_payload["name"],
                "official_name": account_payload.get("official_name") or "",
                "mask": account_payload.get("mask") or "",
                "type": account_payload.get("type") or "",
                "subtype": account_payload.get("subtype") or "",
                "currency_code": account_payload.get("balances", {}).get("iso_currency_code") or "USD",
                "current_balance": _decimal_or_zero(balances.get("current")),
                "available_balance": _decimal_or_none(balances.get("available")),
                "credit_limit": _decimal_or_none(balances.get("limit")),
                "is_active": True,
                "closed_at": None,
                "raw_data": _json_ready(account_payload),
            },
        )

    FinancialAccount.objects.filter(item=item).exclude(external_id__in=seen_ids).update(is_active=False, closed_at=timezone.now())
    return len(seen_ids)


def _fetch_liabilities(*, client: plaid_api.PlaidApi, access_token: str) -> dict[str, Any] | None:
    try:
        return client.liabilities_get(LiabilitiesGetRequest(access_token=access_token)).to_dict()
    except ApiException as exc:
        error_body = _parse_api_body(exc)
        if error_body.get("error_code") in {"PRODUCT_NOT_READY", "NO_LIABILITY_ACCOUNTS"}:
            return None
        raise


def _sync_liabilities(*, item: ConnectedItem, liabilities_payload: dict[str, Any]) -> None:
    liabilities = liabilities_payload.get("liabilities") or {}
    for loan_type, entries in liabilities.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            account_id = entry.get("account_id")
            if not account_id:
                continue
            account = FinancialAccount.objects.filter(item=item, external_id=account_id).first()
            if account is None:
                continue
            LoanLiability.objects.update_or_create(
                account=account,
                defaults={
                    "provider": "plaid",
                    "loan_type": loan_type,
                    "lender_name": entry.get("lender") or entry.get("servicer_address", {}).get("city") or "",
                    "account_number_mask": entry.get("account_number") or account.mask,
                    "interest_rate_percentage": _nested_decimal(entry, "interest_rate_percentage"),
                    "originating_principal_amount": _nested_decimal(entry, "origination_principal_amount"),
                    "last_statement_balance": _nested_decimal(entry, "last_statement_balance"),
                    "last_payment_amount": _nested_decimal(entry, "last_payment_amount"),
                    "minimum_payment_amount": _nested_decimal(entry, "minimum_payment_amount"),
                    "next_payment_due_date": _date_or_none(entry.get("next_payment_due_date")),
                    "last_payment_date": _date_or_none(entry.get("last_payment_date")),
                    "maturity_date": _date_or_none(entry.get("maturity_date")),
                    "raw_data": _json_ready(entry),
                },
            )


def _sync_transactions(*, client: plaid_api.PlaidApi, item: ConnectedItem) -> tuple[int, int, str]:
    next_cursor = item.sync_cursor or ""
    added_count = 0
    updated_count = 0
    has_more = True

    while has_more:
        request = TransactionsSyncRequest(access_token=item.access_token)
        if next_cursor:
            request.cursor = next_cursor
        response = client.transactions_sync(request).to_dict()
        next_cursor = response["next_cursor"]
        has_more = response["has_more"]

        for payload in response.get("added", []):
            account = FinancialAccount.objects.get(item=item, external_id=payload["account_id"])
            _, created = Transaction.objects.update_or_create(
                external_id=payload["transaction_id"],
                defaults={
                    "user": item.user,
                    "item": item,
                    "account": account,
                    "pending_transaction_id": payload.get("pending_transaction_id") or "",
                    "amount": _decimal_or_zero(payload.get("amount")),
                    "iso_currency_code": payload.get("iso_currency_code") or "USD",
                    "date": _date_or_none(payload.get("date")) or timezone.localdate(),
                    "authorized_date": _date_or_none(payload.get("authorized_date")),
                    "name": payload.get("name") or "",
                    "merchant_name": payload.get("merchant_name") or "",
                    "category": payload.get("category") or [],
                    "pending": payload.get("pending", False),
                    "raw_data": _json_ready(payload),
                },
            )
            if created:
                added_count += 1
            else:
                updated_count += 1

        for payload in response.get("modified", []):
            account = FinancialAccount.objects.get(item=item, external_id=payload["account_id"])
            Transaction.objects.update_or_create(
                external_id=payload["transaction_id"],
                defaults={
                    "user": item.user,
                    "item": item,
                    "account": account,
                    "pending_transaction_id": payload.get("pending_transaction_id") or "",
                    "amount": _decimal_or_zero(payload.get("amount")),
                    "iso_currency_code": payload.get("iso_currency_code") or "USD",
                    "date": _date_or_none(payload.get("date")) or timezone.localdate(),
                    "authorized_date": _date_or_none(payload.get("authorized_date")),
                    "name": payload.get("name") or "",
                    "merchant_name": payload.get("merchant_name") or "",
                    "category": payload.get("category") or [],
                    "pending": payload.get("pending", False),
                    "raw_data": _json_ready(payload),
                },
            )
            updated_count += 1

        removed_ids = [payload["transaction_id"] for payload in response.get("removed", []) if payload.get("transaction_id")]
        if removed_ids:
            Transaction.objects.filter(item=item, external_id__in=removed_ids).delete()

    return added_count, updated_count, next_cursor


def ensure_default_user() -> Any:
    user_model = get_user_model()
    user = user_model.objects.order_by("pk").first()
    if user is not None:
        return user
    return user_model.objects.create_user(username="owner", password=user_model.objects.make_random_password())


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _decimal_or_zero(value: Any) -> Decimal:
    return _decimal_or_none(value) or Decimal("0.00")


def _date_or_none(value: Any) -> datetime.date | None:
    if not value:
        return None
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value
    return datetime.fromisoformat(str(value)).date()


def _nested_decimal(data: dict[str, Any], key: str) -> Decimal | None:
    return _decimal_or_none(data.get(key))


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _parse_api_body(exc: ApiException) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body
    if not body:
        return {}
    try:
        import json

        return json.loads(body)
    except Exception:
        return {}


def _extract_api_error(exc: ApiException) -> str:
    payload = _parse_api_body(exc)
    if payload.get("error_message"):
        return str(payload["error_message"])
    return str(exc)