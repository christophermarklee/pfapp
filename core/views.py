from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .models import (
    ConnectedItem,
    Expense,
    ExpenseCategory,
    ExpenseFrequency,
    IncomeCategory,
    IncomeFrequency,
    IncomeSource,
    SyncTrigger,
    Transaction,
    TransferMerchant,
)
from .services import PlaidConfigurationError, create_link_token, exchange_public_token, sync_item


@login_required
@ensure_csrf_cookie
def home(request: HttpRequest) -> HttpResponse:
    response = render(request, "core/home.html", {
        "plaid_configured": bool(settings.PLAID_CLIENT_ID and settings.PLAID_SECRET),
    })
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


# ---------------------------------------------------------------------------
# Plaid endpoints
# ---------------------------------------------------------------------------

@login_required
@require_POST
def plaid_link_token(request: HttpRequest) -> JsonResponse:
    try:
        token = create_link_token(request.user)
    except PlaidConfigurationError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    return JsonResponse({"link_token": token})


@login_required
@require_POST
def plaid_exchange_token(request: HttpRequest) -> JsonResponse:
    payload = _request_payload(request)
    public_token = payload.get("public_token")
    if not public_token:
        return JsonResponse({"error": "public_token is required."}, status=400)

    try:
        item = exchange_public_token(user=request.user, public_token=public_token)
        sync_run = sync_item(item=item, trigger=SyncTrigger.INITIAL)
    except PlaidConfigurationError as exc:
        return JsonResponse({"error": str(exc)}, status=503)

    return JsonResponse({
        "item_id": item.external_id,
        "status": sync_run.status,
        "transactions_added": sync_run.transactions_added,
    })


@login_required
@require_POST
def refresh_item(request: HttpRequest, item_id: int) -> JsonResponse:
    item = get_object_or_404(ConnectedItem, pk=item_id, user=request.user)
    sync_run = sync_item(item=item, trigger=SyncTrigger.MANUAL)
    return JsonResponse({"status": sync_run.status, "accounts_seen": sync_run.accounts_seen})


@csrf_exempt
@require_POST
def plaid_webhook(request: HttpRequest) -> JsonResponse:
    payload = _request_payload(request)
    item_id = payload.get("item_id")
    if not item_id:
        return JsonResponse({"error": "item_id is required."}, status=400)

    item = ConnectedItem.objects.filter(external_id=item_id).first()
    if item is None:
        return JsonResponse({"status": "ignored", "reason": "unknown_item"}, status=202)

    fingerprint = hashlib.sha256(request.body or json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    if item.last_webhook_fingerprint == fingerprint:
        return JsonResponse({"status": "ignored", "reason": "duplicate"}, status=202)

    webhook_type = payload.get("webhook_type", "")
    webhook_code = payload.get("webhook_code", "")
    item.last_webhook_code = f"{webhook_type}:{webhook_code}".strip(":")
    item.last_webhook_fingerprint = fingerprint
    item.raw_data = {**item.raw_data, "last_webhook": payload}
    item.save(update_fields=["last_webhook_code", "last_webhook_fingerprint", "raw_data", "updated_at"])

    if webhook_type == "TRANSACTIONS":
        sync_run = sync_item(item=item, trigger=SyncTrigger.WEBHOOK)
        return JsonResponse({
            "status": sync_run.status,
            "transactions_added": sync_run.transactions_added,
            "transactions_updated": sync_run.transactions_updated,
        }, status=202)

    return JsonResponse({"status": "received", "webhook_code": webhook_code}, status=202)


# ---------------------------------------------------------------------------
# API: accounts
# ---------------------------------------------------------------------------

@login_required
def api_accounts(request: HttpRequest) -> JsonResponse:
    items = (
        ConnectedItem.objects
        .filter(user=request.user)
        .select_related("institution")
        .prefetch_related("accounts")
        .order_by("-updated_at")
    )
    data = []
    for item in items:
        accounts_data = [
            {
                "id": acc.id,
                "name": acc.name,
                "official_name": acc.official_name,
                "mask": acc.mask,
                "type": acc.type,
                "subtype": acc.subtype,
                "current_balance": str(acc.current_balance),
                "available_balance": str(acc.available_balance) if acc.available_balance is not None else None,
                "credit_limit": str(acc.credit_limit) if acc.credit_limit is not None else None,
                "currency_code": acc.currency_code,
            }
            for acc in item.accounts.filter(is_active=True)
        ]
        data.append({
            "id": item.id,
            "institution": item.institution.name if item.institution else "Unknown",
            "institution_color": item.institution.primary_color if item.institution else "",
            "status": item.status,
            "last_sync": item.last_successful_sync_at.isoformat() if item.last_successful_sync_at else None,
            "accounts": accounts_data,
        })
    return JsonResponse({"items": data})


# ---------------------------------------------------------------------------
# API: transactions
# ---------------------------------------------------------------------------

@login_required
def api_transactions(request: HttpRequest) -> JsonResponse:
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except (ValueError, TypeError):
        page = 1
    per_page = 500
    qs = (
        Transaction.objects
        .filter(user=request.user)
        .select_related("account")
        .order_by("-date", "name")
    )
    total = qs.count()
    offset = (page - 1) * per_page
    data = [
        {
            "id": t.id,
            "account": t.account.name,
            "account_mask": t.account.mask,
            "name": t.name,
            "merchant_name": t.merchant_name,
            "amount": str(t.amount),
            "date": t.date.isoformat(),
            "category": t.category,
            "pending": t.pending,
        }
        for t in qs[offset: offset + per_page]
    ]
    return JsonResponse({"transactions": data, "total": total, "page": page, "per_page": per_page})


# ---------------------------------------------------------------------------
# API: expenses
# ---------------------------------------------------------------------------

@login_required
def api_expenses(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        qs = Expense.objects.filter(user=request.user, is_active=True)
        return JsonResponse({"expenses": [_expense_to_dict(e) for e in qs]})

    if request.method == "POST":
        payload = _request_payload(request)
        error = _validate_expense(payload)
        if error:
            return JsonResponse({"error": error}, status=400)
        expense = Expense.objects.create(
            user=request.user,
            name=payload["name"].strip(),
            amount=Decimal(str(payload["amount"])),
            category=payload.get("category") or ExpenseCategory.OTHER,
            frequency=payload.get("frequency") or ExpenseFrequency.MONTHLY,
            due_day=_int_or_none(payload.get("due_day")),
            due_date=payload.get("due_date") or None,
            notes=(payload.get("notes") or "").strip(),
            linked_merchant=(payload.get("linked_merchant") or "").strip(),
        )
        return JsonResponse(_expense_to_dict(expense), status=201)

    return JsonResponse({"error": "Method not allowed."}, status=405)


@login_required
def api_expense_detail(request: HttpRequest, expense_id: int) -> JsonResponse:
    expense = get_object_or_404(Expense, pk=expense_id, user=request.user, is_active=True)

    if request.method == "PUT":
        payload = _request_payload(request)
        error = _validate_expense(payload)
        if error:
            return JsonResponse({"error": error}, status=400)
        expense.name = payload["name"].strip()
        expense.amount = Decimal(str(payload["amount"]))
        expense.category = payload.get("category") or expense.category
        expense.frequency = payload.get("frequency") or expense.frequency
        expense.due_day = _int_or_none(payload.get("due_day"))
        expense.due_date = payload.get("due_date") or None
        expense.notes = (payload.get("notes") or "").strip()
        expense.linked_merchant = (payload.get("linked_merchant") or "").strip()
        expense.save()
        return JsonResponse(_expense_to_dict(expense))

    if request.method == "DELETE":
        expense.is_active = False
        expense.save(update_fields=["is_active", "updated_at"])
        return JsonResponse({"status": "deleted"})

    return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expense_to_dict(expense: Expense) -> dict:
    return {
        "id": expense.id,
        "name": expense.name,
        "amount": str(expense.amount),
        "category": expense.category,
        "category_display": expense.get_category_display(),
        "frequency": expense.frequency,
        "frequency_display": expense.get_frequency_display(),
        "due_day": expense.due_day,
        "due_date": expense.due_date.isoformat() if expense.due_date else None,
        "notes": expense.notes,
        "linked_merchant": expense.linked_merchant,
        "monthly_amount": str(expense.monthly_amount),
    }


def _validate_expense(payload: dict) -> str | None:
    if not str(payload.get("name", "")).strip():
        return "name is required."
    try:
        amount = Decimal(str(payload.get("amount", "")))
        if amount <= 0:
            return "amount must be greater than zero."
    except InvalidOperation:
        return "amount must be a valid number."
    valid_categories = {c[0] for c in ExpenseCategory.choices}
    if payload.get("category") and payload["category"] not in valid_categories:
        return "Invalid category."
    valid_frequencies = {f[0] for f in ExpenseFrequency.choices}
    if payload.get("frequency") and payload["frequency"] not in valid_frequencies:
        return "Invalid frequency."
    return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _request_payload(request: HttpRequest) -> dict:
    # Always parse body as JSON for mutating methods;
    # request.POST is only populated for POST, not PUT/PATCH.
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {key: value for key, value in request.POST.items()}


# ---------------------------------------------------------------------------
# Income Source API
# ---------------------------------------------------------------------------

@login_required
def api_income(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        sources = IncomeSource.objects.filter(user=request.user, is_active=True)
        return JsonResponse({"income_sources": [_income_to_dict(s) for s in sources]})

    if request.method == "POST":
        payload = _request_payload(request)
        err = _validate_income(payload)
        if err:
            return JsonResponse({"error": err}, status=400)
        source = IncomeSource(user=request.user)
        _apply_income(source, payload)
        source.save()
        return JsonResponse({"income_source": _income_to_dict(source)}, status=201)

    return JsonResponse({"error": "Method not allowed."}, status=405)


@login_required
def api_income_detail(request: HttpRequest, source_id: int) -> JsonResponse:
    source = get_object_or_404(IncomeSource, pk=source_id, user=request.user)

    if request.method == "GET":
        return JsonResponse({"income_source": _income_to_dict(source)})

    if request.method == "PUT":
        payload = _request_payload(request)
        err = _validate_income(payload)
        if err:
            return JsonResponse({"error": err}, status=400)
        _apply_income(source, payload)
        source.save()
        return JsonResponse({"income_source": _income_to_dict(source)})

    if request.method == "DELETE":
        source.is_active = False
        source.save(update_fields=["is_active", "updated_at"])
        return JsonResponse({"status": "deleted"})

    return JsonResponse({"error": "Method not allowed."}, status=405)


def _income_to_dict(source: IncomeSource) -> dict:
    npd = source.next_pay_date
    return {
        "id": source.id,
        "name": source.name,
        "amount": str(source.amount),
        "frequency": source.frequency,
        "frequency_display": source.get_frequency_display(),
        "category": source.category,
        "category_display": source.get_category_display(),
        "next_pay_date": npd.isoformat() if hasattr(npd, "isoformat") else (npd or None),
        "day_of_month": source.day_of_month,
        "notes": source.notes,
        "linked_merchant": source.linked_merchant,
        "monthly_amount": str(source.monthly_amount),
        "annual_amount": str(source.annual_amount),
    }


def _validate_income(payload: dict) -> str | None:
    if not str(payload.get("name", "")).strip():
        return "name is required."
    try:
        amount = Decimal(str(payload.get("amount", "")))
        if amount <= 0:
            return "amount must be greater than zero."
    except InvalidOperation:
        return "amount must be a valid number."
    valid_categories = {c[0] for c in IncomeCategory.choices}
    if payload.get("category") and payload["category"] not in valid_categories:
        return "Invalid category."
    valid_frequencies = {f[0] for f in IncomeFrequency.choices}
    if payload.get("frequency") and payload["frequency"] not in valid_frequencies:
        return "Invalid frequency."
    return None


def _apply_income(source: IncomeSource, payload: dict) -> None:
    source.name = str(payload.get("name", "")).strip()
    source.amount = Decimal(str(payload["amount"]))
    source.category = payload.get("category") or IncomeCategory.OTHER
    source.frequency = payload.get("frequency") or IncomeFrequency.MONTHLY
    source.next_pay_date = payload.get("next_pay_date") or None
    source.day_of_month = _int_or_none(payload.get("day_of_month"))
    source.notes = str(payload.get("notes", ""))
    source.linked_merchant = (payload.get("linked_merchant") or "").strip()


@login_required
def api_transfers(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        transfers = list(TransferMerchant.objects.values("id", "merchant_key"))
        return JsonResponse({"transfers": transfers})
    if request.method == "POST":
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        key = (data.get("merchant_key") or "").strip()
        if not key:
            return JsonResponse({"error": "merchant_key required"}, status=400)
        obj, _ = TransferMerchant.objects.get_or_create(merchant_key=key)
        return JsonResponse({"id": obj.id, "merchant_key": obj.merchant_key})
    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
def api_transfer_detail(request: HttpRequest, transfer_id: int) -> JsonResponse:
    obj = get_object_or_404(TransferMerchant, id=transfer_id)
    if request.method == "DELETE":
        obj.delete()
        return JsonResponse({"ok": True})
    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
def api_ai_suggest(request: HttpRequest) -> JsonResponse:
    from .ai import build_merchant_summary, build_expense_context, call_suggest, _safe_merchant_name
    from .models import AISuggestionCache

    # GET — return cached suggestions (no OpenAI call)
    if request.method == "GET":
        cache = AISuggestionCache.objects.filter(user=request.user).first()
        if cache:
            return JsonResponse({
                "suggestions": cache.suggestions,
                "generated_at": cache.generated_at.isoformat(),
                "cached": True,
            })
        return JsonResponse({"suggestions": [], "cached": False})

    # POST — call OpenAI, save result to cache
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    transactions = list(Transaction.objects.filter(user=request.user).order_by("-date"))
    expenses = list(Expense.objects.filter(user=request.user, is_active=True))

    merchant_summary = build_merchant_summary(transactions)
    expense_context = build_expense_context(expenses)

    try:
        suggestions = call_suggest(merchant_summary, expense_context, settings.OPENAI_MODEL)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"AI request failed: {exc}"}, status=500)

    # Build a lookup: safe merchant name -> list of transactions (capped per merchant)
    txn_by_merchant: dict[str, list] = {}
    for t in transactions:
        name = _safe_merchant_name(t)
        if not name:
            continue
        txn_by_merchant.setdefault(name, [])
        if len(txn_by_merchant[name]) < 5:
            txn_by_merchant[name].append({
                "id": t.id,
                "merchant_name": name,
                "date": str(t.date),
                "amount": float(abs(t.amount)),
            })

    # Attach matching transactions to each suggestion
    for s in suggestions:
        txns = []
        seen_ids: set[int] = set()
        for merchant in s.get("linked_merchants", []):
            for txn in txn_by_merchant.get(merchant, []):
                if txn["id"] not in seen_ids:
                    txns.append(txn)
                    seen_ids.add(txn["id"])
        txns.sort(key=lambda x: x["date"], reverse=True)
        s["transactions"] = txns[:10]

    # Save to cache (upsert)
    AISuggestionCache.objects.update_or_create(
        user=request.user,
        defaults={"suggestions": suggestions},
    )

    return JsonResponse({"suggestions": suggestions, "cached": False})
