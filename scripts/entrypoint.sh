#!/usr/bin/env sh
set -eu

until python - <<'PY'
import os
import socket

host = os.environ.get("DATABASE_HOST", "db")
port = int(os.environ.get("DATABASE_PORT", "5432"))

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(1)
    try:
        sock.connect((host, port))
    except OSError:
        raise SystemExit(1)
PY
do
  echo "Waiting for PostgreSQL at ${DATABASE_HOST:-db}:${DATABASE_PORT:-5432}..."
  sleep 1
done

python manage.py migrate --noinput
exec "$@"
