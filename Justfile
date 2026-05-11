rebuild:
    podman compose down -v && podman compose up -d --build --force-recreate

rebuild-code:
    podman compose up -d --build --force-recreate --no-cache

bootstrap-admin password username="admin":
    podman exec pfapp_web_1 python manage.py bootstrap_owner --username {{username}} --password {{password}} --superuser