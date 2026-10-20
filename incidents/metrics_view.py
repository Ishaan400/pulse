import ipaddress

from django.conf import settings
from django.http import HttpResponseNotFound
from django_prometheus.exports import ExportToDjangoView


def metrics(request):
    """Serve Prometheus metrics only to direct requests from a private network.

    Prometheus scrapes web:8000 over the Docker network (private address, no proxy
    headers). Public clients, and anything relayed through a reverse proxy, get 404.
    """
    if not settings.DEBUG:
        try:
            addr = ipaddress.ip_address(request.META.get("REMOTE_ADDR", ""))
        except ValueError:
            return HttpResponseNotFound()
        proxied = "HTTP_X_FORWARDED_FOR" in request.META or "HTTP_X_REAL_IP" in request.META
        if proxied or not (addr.is_private or addr.is_loopback):
            return HttpResponseNotFound()
    return ExportToDjangoView(request)
