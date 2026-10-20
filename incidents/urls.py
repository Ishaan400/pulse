from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("incidents", views.IncidentViewSet, basename="incident")
router.register("notifications", views.NotificationViewSet, basename="notification")
router.register("services", views.ServiceViewSet, basename="service")
router.register("sla-policies", views.SLAPolicyViewSet, basename="slapolicy")
router.register("users", views.UserViewSet, basename="user")

urlpatterns = [
    path("auth/csrf/", views.CsrfView.as_view(), name="csrf"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    path("auth/me/", views.MeView.as_view(), name="me"),
    path("engineers/", views.EngineerListView.as_view(), name="engineers"),
    path("health/", views.HealthView.as_view(), name="health"),
    path("", include(router.urls)),
]
