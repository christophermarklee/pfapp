from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal

from django.conf import settings


VALID_CATEGORIES = {
    "housing",       # rent, mortgage, HOA, home repairs
    "utilities",     # electric, gas/heat, water, internet, phone
    "groceries",     # supermarkets, grocery delivery
    "dining",        # restaurants, takeout, coffee shops, bars
    "transport",     # car, fuel, transit, rideshare, parking
    "health",        # medical, dental, pharmacy, gym, fitness
    "insurance",     # auto, life, home, renters, disability
    "shopping",      # clothing, electronics, general retail
    "entertainment", # streaming services, events, hobbies, travel
    "subscriptions", # SaaS, apps, recurring memberships
    "education",     # tuition, books, courses, childcare/daycare
    "personal",      # haircuts, beauty, hygiene, self-care
    "pets",          # vet, pet food, grooming, supplies
    "debt",          # loan/credit card payments (not mortgage)
    "savings",       # transfers to savings accounts, investments
    "taxes",         # tax payments, government fees, fines
    "other",         # catch-all for unclassifiable items
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
    r"|(\b(?:from|to|payment from|transfer to)\s+[A-Z][a-z]+ [A-Z][a-z]+)"  # "from John Smith"
    r"|X{4,}\d+",                                  # masked account numbers (XXXXXXX7713)
    re.IGNORECASE,
)

# Patterns that indicate raw ACH/wire/bank metadata, internal transfers,
# or bank fees — not real merchant names.
_RAW_BANK_PATTERNS = re.compile(
    r"\bACH\b"                             # ACH prefix
    r"|Entry\s+Class\s+Code"               # ACH metadata
    r"|Trace\s+Number"                     # ACH trace
    r"|CT\s+NAME\s*:"                      # ACH customer name field
    r"|CO\s*:\s*[A-Z]"                     # ACH company field
    r"|TYPE\s*:\s*[A-Z]"                   # ACH type field
    r"|\bWIRE\s+TRANSFER\b"               # wire transfers
    r"|\bZELLE\s+PAYMENT\s+FROM\b"        # P2P with sender name
    r"|(?:[A-Z0-9]{2,}\s*:\s*){3,}"       # 3+ colon-separated fields (raw codes)
    r"|\bTrans\s+(?:To|From)\b"           # CU internal transfers (Trans To, Trans From)
    r"|\b(?:From|To)\s+Share\b"           # CU share transfers (From Share 01, To Share 50)
    r"|\bINTEREST\s+(?:CHARGE|CREDIT)\b" # bank interest charges / credits
    r"|Annual\s+Percentage\s+Yield"       # savings interest income
    r"|\bDividend\b"                      # dividend deposits
    r"|\bSalary/Regular\s+Income\b"       # payroll labels
    r"|\bPayment\s+to\s+[A-Za-z]"        # credit card payments ('Payment to Capital One')
    r"|\bMOBILE\s+PYMT\b"               # credit card mobile payments
    r"|\bONLINE\s+PYMT\b"              # credit card online payments
    r"|\s+(?:VA|MD|DC|CA|TX|NY|FL|PA|NC|GA|OH|MI|NJ|WA|AZ|MA|TN|IN|MO|WI|CO|MN|SC|AL|LA|KY|OR|OK|CT|UT|IA|NV|MS|AR|KS|NE|NM|WV|ID|HI|NH|ME|RI|MT|DE|SD|ND|AK|VT|WY)\s+Date\b"  # city/state + "Date" suffix (MACU raw)
    r"|\b\d{3,5}\s+[A-Za-z]+(?:s|ell|ill|ood|ack)\s+(?:Rd|Dr|Ave|Blvd|St|Ln|Way|Mill|R)\b"  # street addresses
    r"|^#\s",                                                                                  # leading # (raw bank codes)
    re.IGNORECASE,
)


def _safe_merchant_name(t) -> str | None:
    """Return a safe merchant name for AI processing.

    Prefers Plaid's normalized merchant_name; falls back to t.name if it passes
    safety filters (no raw ACH metadata, no PII, not excessively long).
    """
    name = (t.merchant_name or "").strip()
    if not name:
        # Fall back to raw transaction name, but only if it passes safety checks
        name = (t.name or "").strip()
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
        # Skip income/credits (negative Plaid amounts = money coming in)
        if t.amount <= 0:
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
You are a personal finance assistant embedded in a budgeting app. The user has imported bank
transactions and you must analyze their spending to suggest well-organized expense budget categories.

You will receive:
1. A list of merchant spending summaries (merchant name, transaction count, average and total amounts
   rounded to the nearest dollar, last active month YYYY-MM, Plaid categories)
2. A list of existing expense budgets the user already set up (category, frequency, linked merchants)

Return a JSON object with a "suggestions" array. Each item must have:
- "name": Clear, human-readable expense name (e.g. "Netflix & Streaming", "Grocery Shopping",
  "Dining Out"). Be specific but concise. Do NOT include the dollar amount in the name.
- "category": Exactly one of:
    housing       — rent, mortgage, HOA, home repairs/maintenance
    utilities     — electricity, gas/heat, water, internet, phone bill
    groceries     — supermarkets, grocery delivery (Instacart, Whole Foods delivery)
    dining        — restaurants, fast food, coffee shops, bars, takeout/delivery apps
    transport     — car payment, fuel/gas, public transit, rideshare (Uber/Lyft), parking, tolls
    health        — doctor visits, prescriptions, dental, vision, gym membership, therapy
    insurance     — auto, life, home, renters, disability, pet insurance premiums
    shopping      — clothing, electronics, department stores, Amazon (general retail)
    entertainment — movies, concerts, sports, hobbies, vacation/travel, hotels
    subscriptions — streaming video/music/games, software, recurring app charges, news
    education     — tuition, books, online courses, tutoring, daycare, school supplies
    personal      — haircuts, salon, spa, beauty products, hygiene
    pets          — vet bills, pet food, grooming, boarding, supplies
    debt          — credit card payments, student loan, personal loan (not mortgage)
    savings       — transfers to savings/investment/brokerage accounts, 401k contributions
    taxes         — tax payments, government fees, DMV, fines
    other         — only if nothing else fits
- "frequency": One of: weekly, biweekly, semimonthly, monthly, quarterly, annual, one_time, variable
  Use "variable" for spending with no fixed schedule (groceries, dining, gas, shopping, etc.)
  Use "monthly" only if the merchant clearly charges on a recurring fixed schedule.
- "suggested_amount": Number (whole dollars).
  For variable: the estimated monthly spend based on transaction history.
  For fixed frequency: the per-payment amount.
- "linked_merchants": Array of merchant name strings to group under this budget.
  Group related merchants together (e.g. ["McDonald's", "KFC", "Burger King"] → one "Fast Food" budget).
- "reason": One sentence describing the pattern you observed (e.g. "3 transactions/month averaging $47").
- "is_new": true if this is not covered by existing expenses; false if refining an existing one.

Key rules:
- SEPARATE groceries from dining — this is the most common budgeting mistake. A Walmart or
  Whole Foods charge is "groceries"; a DoorDash or Chipotle charge is "dining".
- SEPARATE streaming subscriptions (entertainment/subscriptions) from one-time purchases (shopping).
- DO NOT use "food" or "gas" as categories — they are not valid. Use "groceries" or "transport".
- Group merchants logically: multiple streaming services → one "Streaming Services" budget.
- Skip merchants already matched by existing expenses.
- Return at most 15 suggestions, ranked by total spend (highest first).
- Only include merchants with meaningful spend (≥2 transactions or >$50 total).
- Return valid JSON only. No markdown, no commentary outside the JSON object.
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
