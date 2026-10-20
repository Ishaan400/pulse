"""Create demo users, services and a few incidents. Safe to re-run.

    python manage.py seed_demo --password 'choose-a-password'
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from incidents import services
from incidents.models import Incident, Service

User = get_user_model()

USERS = [
    ("admin", "ADMIN"),
    ("maya.manager", "MANAGER"),
    ("eli.engineer", "ENGINEER"),
    ("sam.engineer", "ENGINEER"),
    ("rio.engineer", "ENGINEER"),
]
SERVICES = [
    ("Payment API", "Card and UPI payment processing"),
    ("Authentication Service", "Login, sessions and SSO"),
    ("Notification Service", "Email, SMS and push delivery"),
]


class Command(BaseCommand):
    help = "Seed demo users, services and incidents"

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="Password for every demo user")

    def handle(self, *args, password, **opts):
        if len(password) < 8:
            raise CommandError("Use a password of at least 8 characters.")
        users = {}
        for username, role in USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@pulse.local", "role": role,
                          "is_staff": role == "ADMIN", "is_superuser": role == "ADMIN"},
            )
            if created:
                user.set_password(password)
                user.save()
            users[username] = user
        svc = {}
        for name, desc in SERVICES:
            svc[name], _ = Service.objects.get_or_create(name=name, defaults={"description": desc})

        if not Incident.objects.exists():
            eli, sam, maya = users["eli.engineer"], users["sam.engineer"], users["maya.manager"]
            a = services.create_incident(
                user=eli, title="Payment API returning HTTP 500",
                description="Checkout requests fail since the 11:40 deploy. Error rate ~18%.",
                severity="HIGH", service=svc["Payment API"])
            services.set_assignees(incident_id=a.pk, engineer_ids=[eli.pk, sam.pk], actor=maya)
            services.change_status(incident_id=a.pk, new_status="IN_PROGRESS", user=eli)
            a.comments.create(user=sam, content="Rolling back to the previous release now.")

            b = services.create_incident(
                user=sam, title="Login latency above 3s for some users",
                description="p95 latency spiked in ap-south-1.", severity="MEDIUM",
                service=svc["Authentication Service"])
            services.set_assignees(incident_id=b.pk, engineer_ids=[sam.pk], actor=maya)

            services.create_incident(
                user=eli, title="Delayed SMS OTP delivery",
                description="Users report OTP SMS arriving after 2+ minutes.",
                severity="CRITICAL", service=svc["Notification Service"])
        self.stdout.write(self.style.SUCCESS(
            "Demo data ready: " + ", ".join(u for u, _ in USERS)))
