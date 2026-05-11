from django.core.management.base import BaseCommand

from core.models import SyncTrigger
from core.services import sync_all_items


class Command(BaseCommand):
    help = "Synchronize all linked Plaid items."

    def handle(self, *args, **options):
        sync_runs = sync_all_items(trigger=SyncTrigger.SCHEDULED)

        if not sync_runs:
            self.stdout.write(self.style.WARNING("No linked items found."))
            return

        successful = sum(1 for sync_run in sync_runs if sync_run.status == "succeeded")
        failed = len(sync_runs) - successful
        self.stdout.write(self.style.SUCCESS(f"Finished {len(sync_runs)} sync runs: {successful} succeeded, {failed} failed."))