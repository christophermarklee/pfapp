from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the single-user owner account used to access the dashboard."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--superuser", action="store_true")

    def handle(self, *args, **options):
        username = options["username"].strip()
        password = options["password"]
        email = options["email"].strip()
        make_superuser = options["superuser"]

        if not username:
            raise CommandError("--username must not be empty.")
        if not password:
            raise CommandError("--password must not be empty.")

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=username)
        if email:
            user.email = email
        user.is_staff = True
        if make_superuser:
            user.is_superuser = True
        user.set_password(password)
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created owner account '{username}'."))
            return

        self.stdout.write(self.style.SUCCESS(f"Updated owner account '{username}'."))