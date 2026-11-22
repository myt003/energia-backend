import time

from django.core.management.base import BaseCommand

from core.services.ai_jobs import process_next_job


class Command(BaseCommand):
    help = "Process Energia's database-backed AI evidence queue"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=1.0)

    def handle(self, *args, **options):
        while True:
            job = process_next_job()
            if job:
                self.stdout.write(f"Processed AI job {job.id}: {job.status}")
            if options["once"]:
                return
            if job is None:
                time.sleep(max(0.2, options["interval"]))
