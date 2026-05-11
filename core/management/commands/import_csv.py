"""
Management command to import transactions from manually-exported CSV files.

Supported formats:
  - Capital One:  Transaction Date, Posted Date, Card No., Description, Category, Debit, Credit
  - Mountain America CU (MACU): Transaction ID, Posting Date, ..., Amount, ..., Description, ...

Usage:
    python manage.py import_csv ./import/          # all CSVs in directory
    python manage.py import_csv ./import/file.csv  # single file
    python manage.py import_csv ./import/ --username admin
"""

from __future__ import annotations

import csv
import hashlib
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.models import ConnectedItem, FinancialAccount, Institution, Transaction

User = get_user_model()


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _detect_format(headers: list[str]) -> str | None:
    if "Transaction Date" in headers and "Debit" in headers and "Card No." in headers:
        return "capitalone"
    if "Transaction ID" in headers and "Posting Date" in headers and "Amount" in headers:
        return "macu"
    return None


# ---------------------------------------------------------------------------
# Capital One helpers
# ---------------------------------------------------------------------------

def _parse_cap1_date(s: str):
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _cap1_external_id(row: dict) -> str:
    key = f"{row['Transaction Date']}|{row['Posted Date']}|{row['Card No.']}|{row['Description']}|{row.get('Debit','')}|{row.get('Credit','')}"
    return "cap1-" + hashlib.sha1(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# MACU helpers
# ---------------------------------------------------------------------------

def _parse_macu_date(s: str):
    return datetime.strptime(s.strip(), "%m/%d/%Y").date()


def _macu_account_segment(transaction_id: str) -> str:
    """Extract the account number from the Transaction ID second segment."""
    parts = transaction_id.strip().split()
    return parts[1] if len(parts) > 1 else "unknown"


_MACU_PREFIX = re.compile(
    r"^(?:Withdrawal Debit |Withdrawal Check |Withdrawal |Deposit ACH |Deposit Dividend |Deposit |Credit |Debit )",
    re.IGNORECASE,
)
_MACU_PHONE = re.compile(r"\s+\d{3}-\d{3}-\d{4}")
_MACU_DATE_SUFFIX = re.compile(r"\s+Date \d{2}/\d{2}/\d{2,4}.*", re.IGNORECASE)
_MACU_CARD_SUFFIX = re.compile(r"\s+(?:xx\s+\d+\s+)?Card \d+$", re.IGNORECASE)


def _clean_macu_merchant(description: str) -> str:
    name = _MACU_PREFIX.sub("", description)
    name = _MACU_PHONE.sub("", name)
    name = _MACU_DATE_SUFFIX.sub("", name)
    name = _MACU_CARD_SUFFIX.sub("", name)
    return name.strip()


def _macu_account_type(type_col: str) -> tuple[str, str]:
    """Return (type, subtype) from the MACU 'Type' column."""
    t = type_col.strip().lower()
    if t == "dividends":
        return "depository", "savings"
    return "depository", "checking"


# ---------------------------------------------------------------------------
# get-or-create helpers
# ---------------------------------------------------------------------------

def _get_or_create_institution(name: str, ext_id: str) -> Institution:
    inst, _ = Institution.objects.get_or_create(
        external_id=ext_id,
        defaults={"name": name, "provider": "plaid"},  # provider field required
    )
    return inst


def _get_or_create_item(user, institution: Institution, ext_id: str) -> ConnectedItem:
    item, _ = ConnectedItem.objects.get_or_create(
        external_id=ext_id,
        defaults={
            "user": user,
            "institution": institution,
            "provider": "plaid",
            "access_token": "",
            "status": "active",
        },
    )
    return item


def _get_or_create_account(
    user,
    item: ConnectedItem,
    institution: Institution,
    ext_id: str,
    name: str,
    acct_type: str,
    subtype: str,
    mask: str,
    balance: Decimal,
) -> FinancialAccount:
    acct, created = FinancialAccount.objects.get_or_create(
        external_id=ext_id,
        defaults={
            "user": user,
            "item": item,
            "institution": institution,
            "name": name,
            "type": acct_type,
            "subtype": subtype,
            "mask": mask,
            "current_balance": balance,
        },
    )
    if not created and balance:
        acct.current_balance = balance
        acct.save(update_fields=["current_balance"])
    return acct


# ---------------------------------------------------------------------------
# Per-format importers
# ---------------------------------------------------------------------------

def _import_capitalone(path: Path, user, stdout, style) -> tuple[int, int]:
    added = skipped = 0
    institution = _get_or_create_institution("Capital One", "manual-capitalone")

    # Group rows by card number so we create one account per card
    rows_by_card: dict[str, list[dict]] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            card = row["Card No."].strip()
            rows_by_card.setdefault(card, []).append(row)

    for card_no, rows in rows_by_card.items():
        item = _get_or_create_item(user, institution, f"manual-capitalone-{card_no}")
        acct = _get_or_create_account(
            user=user, item=item, institution=institution,
            ext_id=f"cap1-card-{card_no}",
            name=f"Capital One ••{card_no}",
            acct_type="credit", subtype="credit card",
            mask=card_no[-4:], balance=Decimal("0"),
        )

        for row in rows:
            ext_id = _cap1_external_id(row)
            debit  = row.get("Debit", "").strip()
            credit = row.get("Credit", "").strip()
            # Plaid convention: positive = money out (expense)
            if debit:
                amount = Decimal(debit)
            elif credit:
                amount = -Decimal(credit)  # refund / payment = negative
            else:
                continue

            date = _parse_cap1_date(row["Transaction Date"])
            name = row["Description"].strip()
            category = [row["Category"].strip()] if row.get("Category", "").strip() else []

            _, created = Transaction.objects.get_or_create(
                external_id=ext_id,
                defaults={
                    "user": user, "item": item, "account": acct,
                    "name": name, "merchant_name": name,
                    "amount": amount, "date": date,
                    "category": category, "pending": False,
                },
            )
            if created:
                added += 1
            else:
                skipped += 1

    stdout.write(f"  Capital One ({path.name}): {added} added, {skipped} skipped")
    return added, skipped


def _import_macu(path: Path, user, stdout, style) -> tuple[int, int]:
    added = skipped = 0
    institution = _get_or_create_institution(
        "Mountain America Credit Union", "manual-macu"
    )

    # Read all rows first to detect account and balance
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        stdout.write(f"  MACU ({path.name}): empty file, skipped")
        return 0, 0

    # Determine account from first row's Transaction ID
    first_txn_id = rows[0]["Transaction ID"]
    acct_segment = _macu_account_segment(first_txn_id)

    # Determine account type from Type column values
    types_seen = {r.get("Type", "").strip() for r in rows}
    sample_type = next(iter(types_seen - {""}), "Card")
    acct_type, subtype = _macu_account_type(sample_type)

    # Balance from first row (most recent transaction = current balance)
    try:
        balance = Decimal(rows[0].get("Balance", "0").replace(",", ""))
    except Exception:
        balance = Decimal("0")

    # Try to extract card mask from descriptions
    mask = acct_segment[-4:]
    for r in rows:
        m = re.search(r"Card (\d{4})\b", r.get("Description", ""))
        if m:
            mask = m.group(1)
            break

    account_name = f"MACU ••{mask}" if subtype == "checking" else f"MACU Savings ••{mask}"
    item_ext_id  = f"manual-macu-{acct_segment}"
    acct_ext_id  = f"macu-acct-{acct_segment}"

    item = _get_or_create_item(user, institution, item_ext_id)
    acct = _get_or_create_account(
        user=user, item=item, institution=institution,
        ext_id=acct_ext_id, name=account_name,
        acct_type=acct_type, subtype=subtype,
        mask=mask, balance=balance,
    )

    for row in rows:
        txn_id = row["Transaction ID"].strip()
        ext_id = "macu-" + hashlib.sha1(txn_id.encode()).hexdigest()[:16]

        try:
            # MACU: negative amount = money out; negate to match Plaid (positive = expense)
            raw_amount = Decimal(row["Amount"].replace(",", ""))
            amount = -raw_amount
        except Exception:
            continue

        try:
            date = _parse_macu_date(row["Posting Date"])
        except Exception:
            continue

        raw_desc = row.get("Description", "").strip()
        merchant  = _clean_macu_merchant(raw_desc)
        name      = merchant or raw_desc
        cat_raw   = row.get("Transaction Category", "").strip()
        category  = [cat_raw] if cat_raw else []

        _, created = Transaction.objects.get_or_create(
            external_id=ext_id,
            defaults={
                "user": user, "item": item, "account": acct,
                "name": name, "merchant_name": merchant,
                "amount": amount, "date": date,
                "category": category, "pending": False,
                "raw_data": {"original_description": raw_desc},
            },
        )
        if created:
            added += 1
        else:
            skipped += 1

    stdout.write(f"  MACU ({path.name}): {added} added, {skipped} skipped")
    return added, skipped


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Import transactions from Capital One or Mountain America CU CSV exports."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            help="Path to a CSV file or a directory containing CSV files.",
        )
        parser.add_argument(
            "--username",
            default=None,
            help="Username to assign imported transactions to (defaults to first superuser).",
        )

    def handle(self, *args, **options):
        # Resolve user
        username = options["username"]
        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"User '{username}' not found.")
        else:
            user = User.objects.filter(is_superuser=True).order_by("id").first()
            if not user:
                raise CommandError("No superuser found. Pass --username explicitly.")

        self.stdout.write(f"Importing as user: {user.username}")

        # Gather CSV files
        target = Path(options["path"]).expanduser().resolve()
        if target.is_dir():
            csv_files = sorted(target.glob("*.csv"))
        elif target.is_file():
            csv_files = [target]
        else:
            raise CommandError(f"Path not found: {target}")

        if not csv_files:
            raise CommandError(f"No CSV files found in {target}")

        total_added = total_skipped = 0

        for csv_path in csv_files:
            # Peek at headers to detect format
            with csv_path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames or []

            fmt = _detect_format(headers)
            if fmt is None:
                self.stdout.write(self.style.WARNING(f"  Skipping {csv_path.name}: unrecognised format"))
                continue

            try:
                if fmt == "capitalone":
                    a, s = _import_capitalone(csv_path, user, self.stdout, self.style)
                else:
                    a, s = _import_macu(csv_path, user, self.stdout, self.style)
                total_added   += a
                total_skipped += s
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ERROR importing {csv_path.name}: {exc}"))
                import traceback; traceback.print_exc(file=sys.stderr)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {total_added} transactions imported, {total_skipped} already existed."
        ))
