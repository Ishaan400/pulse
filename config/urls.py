from django.contrib import admin
from django.urls import include, path

from incidents.metrics_view import metrics

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("incidents.urls")),
    path("metrics", metrics, name="prometheus-metrics"),
    path("", include("web.urls")),
]
