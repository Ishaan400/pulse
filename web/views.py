from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView
from django.utils.decorators import method_decorator


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AppView(TemplateView):
    """Single-page shell. All data comes from the session-authenticated API."""

    template_name = "web/index.html"
