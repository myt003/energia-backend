from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.services.habits import generate_energy_habits
from core.services.tasks import generate_daily_tasks, refresh_overdue_tasks


class Command(BaseCommand):
    help = "Idempotently generate today's tasks for active Energia users"

    def add_arguments(self, parser):
        parser.add_argument("--username", help="Generate tasks for one user")

    def handle(self, *args, **options):
        users = get_user_model().objects.filter(
            memberships__is_active=True,
            work_profile__isnull=False,
        ).distinct()
        if options.get("username"):
            users = users.filter(username=options["username"])
        count = 0
        for user in users:
            count += len(generate_daily_tasks(user=user))
            count += len(generate_energy_habits(user=user)["created"])
            refresh_overdue_tasks(user=user)
        self.stdout.write(self.style.SUCCESS(f"Created {count} task instances"))
