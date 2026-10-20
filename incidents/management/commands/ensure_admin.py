"""Create the first Admin from environment variables if it does not exist.

Reads PULSE_ADMIN_USERNAME, PULSE_ADMIN_EMAIL, PULSE_ADMIN_PASSWORD. Does nothing
if any is missing or the user already exists, so it is safe on every boot.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()


class Command(BaseCommand):
    help = "Create the initial Pulse admin from env vars (idempotent)"

    def handle(self, *args, **opts):
        username = os.environ.get("PULSE_ADMIN_USERNAME")
        email = os.environ.get("PULSE_ADMIN_EMAIL")
        password = os.environ.get("PULSE_ADMIN_PASSWORD")
        if not (username and email and password):
            self.stdout.write("ensure_admin: PULSE_ADMIN_* not set, skipping")
            return
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"ensure_admin: {username} already exists")
            return
        User.objects.create_superuser(username=username, email=email, password=password,
                                      role=User.Role.ADMIN)
        self.stdout.write(self.style.SUCCESS(f"ensure_admin: created admin {username}"))
