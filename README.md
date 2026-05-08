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
podman compose up --build
```

4. Open `http://localhost:8000/`.

## Notes

- The web service uses `python:3.14-slim-bookworm`.
- The database service uses `postgres:latest`.
- The Django settings read database credentials from environment variables.
