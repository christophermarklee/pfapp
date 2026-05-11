# pfapp

Django 6 starter project using Python 3.14, uv, and Podman Compose with PostgreSQL.

## Quick start

1. Copy `.env.example` to `.env` and adjust the values if needed.
2. Install dependencies locally with uv:

```bash
uv sync
```

3. Run the app with Podman Compose:

```bash
podman compose up -d --build
```

4. Open `http://localhost:8000/`.

## Notes

- The web service uses `python:3.14-slim-bookworm`.
- The database service uses `postgres:18.3`.
- The Django settings read database credentials from environment variables.

## Plaid setup

- Add `PLAID_CLIENT_ID` and `PLAID_SECRET` to `.env`.
- Keep `PLAID_ENV=sandbox` while developing.
- `PLAID_PRODUCTS` defaults to `transactions,liabilities`, which matches the current import scope.
- Run `uv run python manage.py bootstrap_owner --username owner --password '<password>'` to create the single-user login for the dashboard.
- Run `uv run python manage.py sync_accounts` to execute the scheduled sync path manually.
