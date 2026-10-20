from celery import shared_task

from . import services


@shared_task(name="incidents.tasks.check_sla_breaches")
def check_sla_breaches():
    return services.check_sla_breaches()
