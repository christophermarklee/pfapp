from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal

from django.conf import settings


VALID_CATEGORIES = {
    "housing", "utilities", "food", "transport", "gas",
    "insurance", "subscriptions", "debt", "medical", "other",
}
VALID_FREQUENCIES = {
    "weekly", "biweekly", "semimonthly", "monthly",
    "quarterly", "annual", "one_time", "variable",
}

# Patterns that suggest raw bank text with potential PII - used to screen out
# merchants that are actually raw transaction descriptions, not business names.
_PII_PATTERNS = re.compile(
    r"\b(\d{3}[-.\s]\d{3}[-.\s]\d{4})"           # phone numbers
    r"|(\b\d{9}\b)"                                # 9-digit numbers (SSN-like)
    r"|([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\."     # email addresses
    r"[a-zA-Z]{2,})"
    r"|(\b(?:from|to|payment from|transfer to)\s+[A-Z][a-z]+ [A-Z][a-z]+)",  # "from John Smith"
    re.IGNORECASE,
)

# Patterns that indicate raw ACH/wire/bank metadata - not real merchant names.
_RAW_BANK_PATTERNS = re.compile(
    r"\bACH\b"                             # ACH prefix
    r"|Entry\s+Class\s+Code"               # ACH metadata
    r"|Trace\s+Number"                     # ACH trace
    r"|CT\s+NAME\s*:"                      # ACH customer name field
    r"|CO\s*:\s*[A-Z]"                     # ACH company field
    r"|TYPE\s*:\s*[A-Z]"                   # ACH type field
    r"|\bWIRE\s+TRANSFER\b"               # wire transfers
    r"|\bZELLE\s+PAYMENT\s+FROM\b"        # P2P with sender name
    r"|(?:[A-Z0-9]{2,}\s*:\s*){3,}",      # 3+ colon-separated fields (raw codes)
    re.IGNORECASE,
)


def _safe_merchant_name(t) -> str | None:
    """Return only the Plaid-normalized merchant_name; skip raw bank descriptions."""
    # Prefer merchant_name — this is Plaid's cleaned, standardized business name.
    # Never fall back to t.name which can contain raw bank text with personal info.
    name = (t.merchant_name or "").strip()
    if not name:
        return None
    # Reject anything that looks like a raw ACH/bank transaction description.
    if _RAW_BANK_PATTERNS.search(name):
        return None
    # Drop anything that pattern-matches as a raw description containing PII.
    if _PII_PATTERNS.search(name):
        return None
    # Reject implausibly long strings — real merchant names are short.
    if len(name) > 60:
        return None
    return name


def build_merchant_summary(transactions: list) -> list[dict]:
    """Aggregate transactions by merchant name into compact spending stats.

    Only Plaid-normalized merchant names are used — raw transaction descriptions
    (t.name) are never sent to OpenAI as they can contain personal information.
    Amounts are rounded to the nearest dollar and dates truncated to month/year.
    """
    grouped: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "total": Decimal("0"), "months": set(), "categories": set()
    })

    for t in transactions:
        name = _safe_merchant_name(t)
        if not name:
            continue
        grouped[name]["count"] += 1
        grouped[name]["total"] += abs(t.amount)
        # Truncate to YYYY-MM — no exact day sent to OpenAI
        grouped[name]["months"].add(str(t.date)[:7])
        if t.category:
            cats = t.category if isinstance(t.category, list) else [t.category]
            for c in cats:
                grouped[name]["categories"].add(str(c))

    result = []
    for name, data in grouped.items():
        avg = float(data["total"]) / data["count"]
        last_month = sorted(data["months"])[-1] if data["months"] else ""
        result.append({
            "merchant": name,
            "count": data["count"],
            # Round to nearest dollar — no cent-level precision sent
            "avg_amount": round(avg),
            "total": round(float(data["total"])),
            "last_month": last_month,          # YYYY-MM only, not exact date
            "categories": sorted(data["categories"])[:3],
        })

    return sorted(result, key=lambda x: x["total"], reverse=True)


def build_expense_context(expenses: list) -> list[dict]:
    """Summarize existing expenses for context.

    Expense names are omitted — they are user-defined and may contain personal
    information. Only category, frequency, and linked merchant patterns are sent.
    """
    return [
        {
            # name intentionally excluded — user text, potential PII
            "category": e.category,
            "frequency": e.frequency,
            "linked_merchants": [m.strip() for m in e.linked_merchant.split("|") if m.strip()],
        }
        for e in expenses
        if e.is_active
    ]


_SYSTEM_PROMPT = """\
You are a personal finance assistant. The user has imported bank transactions.
Your job is to analyze their spending patterns and suggest expense budget categories.

You will receive:
1. A list of merchant spending summaries (merchant name, number of transactions, average and total amounts rounded to the nearest dollar, last active month as YYYY-MM, Plaid categories)
2. A list of existing expense budgets the user has already set up (category, frequency, and linked merchants only)

Return a JSON object with a "suggestions" array. Each suggestion must have:
- "name": clear, human-readable expense name (e.g. "Netflix Subscription", "Grocery Shopping")
- "category": one of: housing, utilities, food, transport, gas, insurance, subscriptions, debt, medical, other
- "frequency": one of: weekly, biweekly, semimonthly, monthly, quarterly, annual, one_time, variable
  Use "variable" for spending that has no fixed schedule — groceries, gas, restaurants, etc.
- "suggested_amount": number (for variable frequency, this is the monthly budget cap; for others it is the per-payment amount)
- "linked_merchants": array of merchant name strings that should be grouped under this expense
- "reason": one sentence explaining the pattern you noticed
- "is_new": true if this is a new suggestion not covered by existing expenses, false if refining an existing one

Rules:
- Group related merchants together (e.g. multiple grocery stores into one "Groceries" expense)
- Base suggested_amount on the average transaction amount for that frequency
- Skip merchants that clearly already match an existing expense's linked_merchants
- Return at most 15 suggestions, prioritized by total spend
- Only include merchants with meaningful recurring or significant spend
- Return valid JSON only, no commentary outside the JSON
"""


def call_suggest(merchant_summary: list[dict], expense_context: list[dict], model: str) -> list[dict]:
    """Call OpenAI and return parsed suggestions list."""
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not configured. Add it to your .env file.")

    try:
        import openai
    except ImportError:
        raise ValueError("openai package is not installed. Run: uv add openai")

    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

    user_content = json.dumps({
        "merchant_spending": merchant_summary[:80],  # cap to keep token count reasonable
        "existing_expenses": expense_context,
    }, default=str)

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        max_completion_tokens=4096,
    )

    raw = response.choices[0].message.content or "{}"
    data = json.loads(raw)
    suggestions = data.get("suggestions", [])

    # Sanitize: ensure category and frequency are valid values
    cleaned = []
    for s in suggestions:
        s["category"] = s.get("category", "other") if s.get("category") in VALID_CATEGORIES else "other"
        s["frequency"] = s.get("frequency", "monthly") if s.get("frequency") in VALID_FREQUENCIES else "monthly"
        s["suggested_amount"] = float(s.get("suggested_amount", 0) or 0)
        # Filter linked_merchants: drop any that look like raw bank descriptions or are too long
        raw_merchants = [str(m) for m in s.get("linked_merchants", [])]
        s["linked_merchants"] = [
            m for m in raw_merchants
            if len(m) <= 60
            and not _RAW_BANK_PATTERNS.search(m)
            and not _PII_PATTERNS.search(m)
        ]
        s["is_new"] = bool(s.get("is_new", True))
        s["reason"] = str(s.get("reason", ""))
        s["name"] = str(s.get("name", "Unknown"))
        cleaned.append(s)

    return cleaned
