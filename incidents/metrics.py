"""Business metrics computed from PostgreSQL at scrape time.

Computing at scrape time (rather than incrementing counters) keeps the numbers
correct across multiple gunicorn workers and the Celery worker process.
"""
import logging

from django.db import DatabaseError
from django.db.models import Count
from prometheus_client.core import REGISTRY, GaugeMetricFamily

logger = logging.getLogger(__name__)


class IncidentCollector:
    def collect(self):
        from .models import Incident

        by_state = GaugeMetricFamily(
            "pulse_incidents", "Incidents by status and severity", labels=["status", "severity"]
        )
        breached = GaugeMetricFamily(
            "pulse_incidents_sla_breached_open",
            "Unresolved incidents that have breached their SLA",
        )
        try:
            rows = Incident.objects.values("status", "severity").annotate(n=Count("id"))
            for row in rows:
                by_state.add_metric([row["status"], row["severity"]], row["n"])
            breached.add_metric(
                [],
                Incident.objects.filter(
                    status__in=["OPEN", "IN_PROGRESS"], sla_breached=True
                ).count(),
            )
        except DatabaseError:  # database not ready (e.g. before migrations)
            logger.debug("Skipping incident metrics; database unavailable")
            return
        yield by_state
        yield breached

    def describe(self):
        return []  # skip the describe-time collect so registration never hits the DB


_collector = IncidentCollector()
try:
    REGISTRY.register(_collector)
except ValueError:
    pass  # already registered (autoreload)
