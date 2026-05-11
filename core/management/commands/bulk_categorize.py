"""
Management command: bulk_categorize
Creates Expense records and TransferMerchant records to categorize
all uncategorized imported transactions.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import Expense, ExpenseCategory, ExpenseFrequency, TransferMerchant

User = get_user_model()

# ── TRANSFERS ─────────────────────────────────────────────────────────────────
# These are internal account transfers — should not count as expenses.
TRANSFERS = [
    "Transfer to Apple Cash",
    "Trans To LEE,SAWYER H XXXXXXX0739",
    "Trans To LEE,RONALD E XXXXXXX7713  S50 TO S50Thanks Dad 0050",
    "To Share 50",
    "Transfer to Cash App",
    "INTEREST CHARGE:PURCHASES",
    "INTEREST CHARGE:CASH ADVANCES",
]

# ── EXPENSES ──────────────────────────────────────────────────────────────────
# (name, amount, frequency, category, linked_merchants pipe-separated)
EXPENSES = [
    # ── Utilities ─────────────────────────────────────────────────────────────
    (
        "AT&T Phone",
        Decimal("176.05"), "monthly", "utilities",
        "ATT*BILL PAYMENT",
    ),
    (
        "Verizon",
        Decimal("99.99"), "monthly", "utilities",
        "Payment to Verizon|ACH V TYPE: PAYMENTREC CO: VERIZON    Entry Class Code: PPD    ACH Trace Number: 3",
    ),
    (
        "Rappahannock Electric",
        Decimal("374.86"), "monthly", "utilities",
        "Payment to Rappahannock Electric Cooperative|ACH R TYPE: PAYMENT CO: RAPP ELEC    NAME: Christopher Lee    Entry Class Code: WEB    ACH Trace Number: 6",
    ),
    (
        "AmeriGas Propane",
        Decimal("1724.92"), "variable", "utilities",
        "ACH A TYPE: UTILITY CO: AMERIGAS NAME: CHRISTOPHER *LEE Entry Class Code: WEB ACH Trace Number: 3",
    ),
    (
        "NumberBarn (Phone Number)",
        Decimal("8.57"), "monthly", "utilities",
        "NUMBERBARN",
    ),

    # ── Insurance ─────────────────────────────────────────────────────────────
    (
        "Travelers Insurance",
        Decimal("1526.00"), "annual", "insurance",
        "Ach T Type: Per Insur CO: Travelers Name: Christopher Lee Christ Entry Class Code: Web Ach Trace Number: 9",
    ),

    # ── Subscriptions ─────────────────────────────────────────────────────────
    (
        "Apple Services",
        Decimal("54.65"), "monthly", "subscriptions",
        "Apple|APPLE.COM/US|Apple.com|APPLE.COM/BILL CA|Amazon Digital Services|#16700 APPLE COM ONE APPLE PARK WAY ONE APPLE PAR CA|AMAZON PRIME*MU3RG3PN3",
    ),
    (
        "GitHub",
        Decimal("21.80"), "monthly", "subscriptions",
        "GITHUB, INC.",
    ),
    (
        "OVH Server Hosting",
        Decimal("142.80"), "monthly", "subscriptions",
        "OVH US LLC",
    ),
    (
        "Google Workspace",
        Decimal("43.11"), "monthly", "subscriptions",
        "GOOGLE *Workspace_agil",
    ),
    (
        "Discord",
        Decimal("35.00"), "monthly", "subscriptions",
        "DISCORD* 12XSERVERBOOS|DISCORD* NITROMONTHLY,",
    ),
    (
        "DatHost (Game Servers)",
        Decimal("9.45"), "monthly", "subscriptions",
        "DATHOST",
    ),
    (
        "Nebula",
        Decimal("30.00"), "monthly", "subscriptions",
        "NEBULA SUBSCRIPTION",
    ),
    (
        "OpenAI ChatGPT",
        Decimal("20.00"), "monthly", "subscriptions",
        "OPENAI *CHATGPT SUBSCR|OPENAI",
    ),
    (
        "CheapSSLShop",
        Decimal("84.00"), "annual", "subscriptions",
        "CHEAPSSLSHOP.COM",
    ),
    (
        "Lose It! (Health App)",
        Decimal("39.99"), "annual", "subscriptions",
        "LOSE IT!",
    ),
    (
        "TMNA Subscription",
        Decimal("15.00"), "monthly", "subscriptions",
        "TMNA SUBSCRIPTION",
    ),
    (
        "SuccessfulMatch",
        Decimal("69.99"), "monthly", "subscriptions",
        "SUCCESSFULMATCH",
    ),
    (
        "Planet Fitness",
        Decimal("10.00"), "monthly", "subscriptions",
        "Ach P Type: Iclub Fees CO: Pf Fredericksbur Entry Class Code: Ach Trace Number: 0|Ach P Type: Iclub Fees CO: Pf Fredericksbur Entry Class Code: Ach Trace Number: 5|Ach P Type: Iclub Fees CO: Pf Fredericksbur Entry Class Code: Ach Trace Number: 9|ACH P TYPE: IClub Fees CO: PF FREDERICKSBUR Entry Class Code: ACH Trace Number: 5",
    ),
    (
        "Canon Ink Club",
        Decimal("4.99"), "monthly", "subscriptions",
        "WWW.USA.CANON.COM",
    ),
    (
        "Laview Security Camera",
        Decimal("7.69"), "monthly", "subscriptions",
        "Laview Technology Laviewsecurit CA Date|LAVIEW TECHNOLOGY LAVIEWSECURIT CA Date|LAVIEW TECHNOLOGY LAVIEWSECURIT CA",
    ),

    # ── Health ────────────────────────────────────────────────────────────────
    (
        "AMFAMFIT Gym",
        Decimal("60.00"), "monthly", "health",
        "CPP*AMFAMFIT FREDERICK",
    ),
    (
        "Alliance Behavioral Psychiatry",
        Decimal("50.00"), "variable", "health",
        "ALLIANCE BEHAV PSYCHIA",
    ),
    (
        "MindBody / Fitness Classes",
        Decimal("49.00"), "variable", "health",
        "MINDBODY PAYMENTS",
    ),
    (
        "Urgent Care",
        Decimal("25.00"), "variable", "health",
        "MWHC URGENT CARE - STA",
    ),
    (
        "CVS Pharmacy",
        Decimal("9.05"), "variable", "health",
        "CVS/PHARMACY #02129",
    ),
    (
        "Wegmans Rx (Mail Prescriptions)",
        Decimal("20.00"), "variable", "health",
        "WEGMANS RX HOME SHIPPI",
    ),

    # ── Housing / Home ────────────────────────────────────────────────────────
    (
        "RJ Home Services",
        Decimal("864.67"), "variable", "housing",
        "# Rj Home Services X3130 Blackwells Mill R Goldvein VA",
    ),
    (
        "Lawn Care (Zelle)",
        Decimal("95.00"), "variable", "housing",
        "Home  MACU: Payment to ZELLE LAWN CARE from    CHRISTOPHER M LEE.",
    ),
    (
        "Go-Forth Pest Control",
        Decimal("44.63"), "variable", "housing",
        "PY *GO-FORTH PEST CONT|PY *GO-FORTH PEST CON|PY *GO-FORTH HOME SERV",
    ),
    (
        "Extra Space Storage",
        Decimal("320.00"), "monthly", "housing",
        "EXTRA SPACE 1777",
    ),
    (
        "Precision Garage Door",
        Decimal("1468.00"), "one_time", "housing",
        "Precision Garage Door",
    ),
    (
        "SupplyHouse.com",
        Decimal("155.67"), "variable", "housing",
        "SUPPLYHOUSE.COM",
    ),
    (
        "Lendervend Appraisal",
        Decimal("590.00"), "one_time", "housing",
        "LENDERVEND APPRAISAL",
    ),
    (
        "Moving Help",
        Decimal("332.65"), "one_time", "housing",
        "MOVINGHELP.COM",
    ),
    (
        "U-Haul",
        Decimal("67.28"), "variable", "housing",
        "U-HAUL MOVING & STORAG",
    ),
    (
        "Smith's Body Shop",
        Decimal("288.00"), "one_time", "transport",
        "SMITHS BODY SHOP",
    ),
    (
        "Lake of the Woods HOA",
        Decimal("6.00"), "variable", "housing",
        "LAKE OF THE WOODS",
    ),

    # ── Transport ─────────────────────────────────────────────────────────────
    (
        "Costco Gas",
        Decimal("35.00"), "variable", "transport",
        "Costco Gas Stations|COSTCO GAS #0340 FREDERICKSBUR VA|GIANT FUEL 0235",
    ),
    (
        "Uber",
        Decimal("67.47"), "variable", "transport",
        "UBER   *TRIP",
    ),
    (
        "Go Car Wash",
        Decimal("28.50"), "monthly", "transport",
        "GO CAR WASH VA-410",
    ),
    (
        "Gas Stations",
        Decimal("50.00"), "variable", "transport",
        "FAS MART 48|Fas Mart|FAS MART 35|SHEETZ 2323|SHEETZ 0323|SHEETZ 2221|PILOT 159|Royal Farms",
    ),
    (
        "Tolls",
        Decimal("63.24"), "variable", "transport",
        "TSI-FHIT-Tolls",
    ),
    (
        "Toyota (Car Payment/Service)",
        Decimal("195.71"), "variable", "transport",
        "Toyota",
    ),
    (
        "DMV",
        Decimal("12.50"), "variable", "taxes",
        "DEPARTMENT MOTOR VEHIC",
    ),

    # ── Gaming (Entertainment) ────────────────────────────────────────────────
    (
        "Steam",
        Decimal("25.00"), "variable", "entertainment",
        "WL *STEAM PURCHASE|STEAMGAMES.COM 4259522",
    ),
    (
        "Regal Cinemas",
        Decimal("40.92"), "variable", "entertainment",
        "REGAL CINEMAS INC",
    ),
    (
        "Ski Center",
        Decimal("629.58"), "variable", "entertainment",
        "Ski Center",
    ),
    (
        "Virginia Lottery",
        Decimal("25.00"), "variable", "entertainment",
        "Virginia Lottery",
    ),

    # ── Dining ────────────────────────────────────────────────────────────────
    (
        "Dining Out",
        Decimal("50.00"), "variable", "dining",
        "BURGER KING #9472  Q07|BURGER KING #9472 Q07|DOMINO'S 4235|WENDYS 11450|McDonald's|McDonalds 26912|STARBUCKS 282 WA|STARBUCKS STORE 09677|GRUBHUB*MCALISTERSDELI|GRUBHUB*CHIPOTLE|GRUBHUB*JERSEYMIKES|DD *DOORDASH HABITBURG|BOB EVANS REST #0537|JERSEY MIKES 6062|Jersey Mike's Subs|MCALISTERS 102359|TST*GUACAMOLE RESTAURA|TST*HARRYS ALEHOUSE|TST* AGORA DOWNTOWN CO|BELLA CUCINA MEXICAN R|The Habit Burger Grill|LINS GARDEN|Native Plate|BJ'SRESTAURANTS MOBILE|DD/BR #354082 Q35|TST*COOL ZONE ICE CREA|7-ELEVEN 37564|7-ELEVEN 19321",
    ),
    (
        "Wegmans Grocery",
        Decimal("30.00"), "variable", "groceries",
        "WEGMANS #41|WEGMANS #41 FREDERICKSBUR VA",
    ),
    (
        "Costco",
        Decimal("155.25"), "variable", "shopping",
        "Costco",
    ),

    # ── Shopping ─────────────────────────────────────────────────────────────
    (
        "Amazon",
        Decimal("60.00"), "variable", "shopping",
        "AMAZON MKTPL*EG62A1YE3|AMAZON MKTPL*BG1FJ30W2|AMAZON MKTPL*BC3249I12|Amazon.com*B56CN4CY2|AMAZON MKTPL*NF7GF2DB3|AMAZON MKTPL*2T5MA9553|AMAZON MKTPL*BE92M4KJ2|AMAZON MKTPL*BD5576TZ2|AMAZON MKTPL*B59Q516T2|AMAZON MKTPL*D07338IV3|AMAZON MKTPL*BY3NB5UY1|AMAZON MKTPL*BS45X7GT2|AMAZON MKTPL*BE5J032A0|AMAZON MKTPL*HK8HA2DG3|AMAZON MKTPL*B994W3YV2|Amazon.com*B58BJ2510|AMAZON MKTPL*B171L8YT0|AMAZON MKTPL*BP7X19VM1|AMAZON MKTPL*S48UL2RW3|AMAZON MKTPL*B979H2RM1|AMAZON MKTPL*BS3N716O0|AMAZON MKTPL*BP2277GV1|AMAZON MKTPL*BE1J35RS0|Amazon.com*BS0BZ8QE2|AMAZON MKTPL*B94JJ5TJ1|AMAZON MKTPL*BD7BC8VH2|AMAZON MKTPL*BV6VL2QP2|AMAZON MKTPL*BJ1CC85E1|AMAZON MKTPL*BY96H7YO0|AMAZON MKTPL*BP6LM8HS2|AMAZON MKTPL*JW0A14623|AMAZON MKTPL*L71BI5903|Amazon.com*B93RK0TF1|AMAZON MKTPL*BY5A03VN0|Amazon.com*BG9LR0CH2|AMAZON MKTPL*BE5PH37L1|AMAZON MKTPL*BJ4J71T30|AMAZON MKTPL*BC3QU5X31|AMAZON MKTPL*BE3LT5A20|AMAZON MKTPL*WY7VW0EX3|AMAZON MKTPL*AY4YL8X33|AMAZON MKTPL*LY6DF6XG3|AMAZON MKTPL*BJ2JI9T10|AMAZON MKTPL*BP2CO9SR0|AMAZON MKTPL*BY0ZV0BH1|AMAZON MKTPL*BP2Z585M2|AMAZON MKTPL*BV3XC5BM2|AMAZON MKTPL*BY7KJ0Y41|AMAZON MKTPL*BV7NV09I2|AMAZON MKTPL*BE8QV1NO0|AMAZON MKTPL*BE5PH37L1|Amazon.com*XN8K362C3|AMAZON MKTPL*BE3LT5A20|AMAZON MKTPL*B79H2RM1|Amazon Digital Services",
    ),
    (
        "Home Depot",
        Decimal("99.76"), "variable", "shopping",
        "THE HOME DEPOT #4660",
    ),
    (
        "Locust Grove Hardware",
        Decimal("71.96"), "variable", "shopping",
        "LOCUST GROVE HARDWARE",
    ),
    (
        "Barnes & Noble",
        Decimal("62.44"), "variable", "shopping",
        "BARNES & NOBLE #2195",
    ),
    (
        "Ollie's Bargain Outlet",
        Decimal("292.36"), "variable", "shopping",
        "OLLIES BARGAIN OUTLET",
    ),
    (
        "Walmart",
        Decimal("56.36"), "variable", "shopping",
        "POS # WAL-MART #5731 2533 GERMANNA HWY LOCUST    GROVE VA",
    ),
    (
        "Mouser Electronics",
        Decimal("40.26"), "variable", "shopping",
        "MOUSER ELECTRONICS INC",
    ),
    (
        "Dollar Tree",
        Decimal("4.74"), "variable", "shopping",
        "Dollar Tree",
    ),

    # ── Pets ─────────────────────────────────────────────────────────────────
    (
        "Pet Grooming (All Fur)",
        Decimal("108.68"), "variable", "pets",
        "SQ *ALL FUR PET GROOMI",
    ),

    # ── Taxes ────────────────────────────────────────────────────────────────
    (
        "H&R Block Tax Software",
        Decimal("26.27"), "annual", "taxes",
        "H&R BLOCK TAX SOFTWARE",
    ),
    (
        "IRS Tax Payment",
        Decimal("545.02"), "annual", "taxes",
        "Tax Payment to IRS",
    ),

    # ── Other / Misc ─────────────────────────────────────────────────────────
    (
        "GoFundMe",
        Decimal("233.00"), "one_time", "other",
        "GFM*GoFundMe Help Darl",
    ),
    (
        "USPS",
        Decimal("11.95"), "variable", "other",
        "USPS PO 5152440508",
    ),
    (
        "Culpeper Steam (Laundromat)",
        Decimal("25.58"), "variable", "other",
        "CULPEPER STM 4 CULPEPER VA",
    ),
    (
        "Electronic First (Electronics)",
        Decimal("7.47"), "variable", "shopping",
        "Electronic First FZ LL",
    ),
    (
        "Loaded.com",
        Decimal("16.69"), "variable", "other",
        "LOADED.COM",
    ),
    (
        "Tobacco / Vape",
        Decimal("30.00"), "variable", "other",
        "TOBACCO HUT & VAPE-THU|KING G TOBACCO & VAPE|PMUSA 153020 RICHMOND",
    ),
    (
        "IDP F (Domain/Software)",
        Decimal("2.95"), "monthly", "subscriptions",
        "IDP F",
    ),
    (
        "OF Subscription",
        Decimal("7.99"), "monthly", "subscriptions",
        "OF",
    ),
    (
        "Vending Machine",
        Decimal("2.00"), "variable", "other",
        "Nayax **Triple P Vendi",
    ),
]


class Command(BaseCommand):
    help = "Bulk-create expenses and mark transfers to categorize imported transactions"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print what would be created without saving")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        user = User.objects.first()
        if not user:
            self.stderr.write("No users found.")
            return

        # ── Mark transfers ─────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING("=== Transfers ==="))
        for merchant in TRANSFERS:
            key = merchant.lower().strip()
            if TransferMerchant.objects.filter(merchant_key=key).exists():
                self.stdout.write(f"  SKIP (exists) transfer: {merchant}")
                continue
            if not dry_run:
                TransferMerchant.objects.create(merchant_key=key)
            self.stdout.write(self.style.SUCCESS(f"  {'[DRY] ' if dry_run else ''}Transfer: {merchant}"))

        # ── Create expenses ────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Expenses ==="))
        created = skipped = 0
        for (name, amount, freq, cat, merchants) in EXPENSES:
            if Expense.objects.filter(user=user, name=name).exists():
                self.stdout.write(f"  SKIP (exists): {name}")
                skipped += 1
                continue
            if not dry_run:
                Expense.objects.create(
                    user=user,
                    name=name,
                    amount=amount,
                    frequency=freq,
                    category=cat,
                    linked_merchant=merchants,
                    is_active=True,
                )
            self.stdout.write(self.style.SUCCESS(f"  {'[DRY] ' if dry_run else ''}Created: {name} — ${amount} / {freq} [{cat}]"))
            created += 1

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nDone. {'Would create' if dry_run else 'Created'} {created} expenses, skipped {skipped}. "
            f"Marked {len(TRANSFERS)} transfer patterns."
        ))
