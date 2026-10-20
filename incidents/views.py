from django.contrib.auth import authenticate, get_user_model, login, logout
from django.db import transaction
from django.db.models import Prefetch
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import ai, services
from .models import Comment, Incident, IncidentAssignment, Notification, Service, SLAPolicy
from .permissions import (
    CanCreateIncident,
    IsAdminRole,
    IsManager,
    ReadOnlyOrAdmin,
    can_comment,
    can_edit_details,
    visible_incidents,
)
from .serializers import (
    AssignSerializer,
    CommentSerializer,
    HistorySerializer,
    IncidentCreateSerializer,
    IncidentSerializer,
    IncidentUpdateSerializer,
    LoginSerializer,
    NotificationSerializer,
    ServiceSerializer,
    SLAPolicySerializer,
    UserSerializer,
    UserSummarySerializer,
)

User = get_user_model()


# --- Session authentication ----------------------------------------------------
@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(request, **data.validated_data)
        if user is None:
            return Response({"detail": "Invalid username or password."},
                            status=status.HTTP_400_BAD_REQUEST)
        login(request, user)  # creates the server-side session + sessionid cookie
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


# --- Users & services ---------------------------------------------------------------
class UserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                  mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Admin user management. Users are deactivated (is_active=false), never deleted,
    so that incident history keeps pointing at real people."""

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminRole]
    http_method_names = ["get", "post", "patch"]


class EngineerListView(APIView):
    """Engineers available for assignment (managers and admins)."""

    def get(self, request):
        if not (request.user.is_manager or request.user.is_admin_role):
            raise PermissionDenied()
        qs = User.objects.filter(role=User.Role.ENGINEER, is_active=True)
        return Response(UserSummarySerializer(qs, many=True).data)


class ServiceViewSet(viewsets.ModelViewSet):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [ReadOnlyOrAdmin]
    pagination_class = None


class SLAPolicyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SLAPolicy.objects.all()
    serializer_class = SLAPolicySerializer
    pagination_class = None


# --- Incidents ---------------------------------------------------------------------
class IncidentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Incidents are never deleted: the audit trail must persist."""

    serializer_class = IncidentSerializer
    permission_classes = [IsAuthenticated, CanCreateIncident]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        qs = (
            visible_incidents(self.request.user)
            .select_related("service", "created_by")
            .prefetch_related(
                Prefetch("assignments",
                         queryset=IncidentAssignment.objects.select_related("engineer", "assigned_by"))
            )
        )
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))
        if p.get("severity"):
            qs = qs.filter(severity__in=p["severity"].split(","))
        if p.get("service"):
            qs = qs.filter(service_id=p["service"])
        if p.get("mine") == "true":
            qs = qs.filter(assignments__engineer=self.request.user)
        if p.get("breached") == "true":
            qs = qs.filter(sla_breached=True)
        if p.get("unassigned") == "true":
            qs = qs.filter(assignments__isnull=True)
        return qs

    def _fresh(self, pk):
        return self.get_queryset().get(pk=pk)

    # POST /api/incidents/
    def create(self, request):
        data = IncidentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        incident = services.create_incident(user=request.user, **data.validated_data)
        return Response(IncidentSerializer(self._fresh(incident.pk)).data,
                        status=status.HTTP_201_CREATED)

    # PATCH /api/incidents/{id}/
    def partial_update(self, request, pk=None):
        incident = self.get_object()  # 404 if the user cannot see it
        data = IncidentUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        changes = dict(data.validated_data)
        new_status = changes.pop("status", None)

        with transaction.atomic():
            if changes:
                if not can_edit_details(request.user, incident):
                    raise PermissionDenied("You cannot edit this incident's details.")
                for field, value in changes.items():
                    setattr(incident, field, value)
                incident.save(update_fields=list(changes))
            if new_status:
                services.change_status(incident_id=incident.pk, new_status=new_status,
                                       user=request.user)
        return Response(IncidentSerializer(self._fresh(incident.pk)).data)

    # PATCH /api/incidents/{id}/assign/  {"engineer_ids": [20, 21]}
    @action(detail=True, methods=["patch"], permission_classes=[IsManager])
    def assign(self, request, pk=None):
        incident = self.get_object()
        data = AssignSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        _, added, removed = services.set_assignees(
            incident_id=incident.pk, engineer_ids=data.validated_data["engineer_ids"],
            actor=request.user,
        )
        body = IncidentSerializer(self._fresh(incident.pk)).data
        body["added"] = [u.username for u in added]
        body["removed"] = [u.username for u in removed]
        return Response(body)

    # GET /api/incidents/{id}/history/
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        incident = self.get_object()
        return Response(HistorySerializer(incident.history.select_related("user"), many=True).data)

    # GET|POST /api/incidents/{id}/comments/
    @action(detail=True, methods=["get", "post"])
    def comments(self, request, pk=None):
        incident = self.get_object()
        if request.method == "GET":
            qs = incident.comments.select_related("user")
            return Response(CommentSerializer(qs, many=True).data)
        if not can_comment(request.user, incident):
            raise PermissionDenied("You cannot comment on this incident.")
        data = CommentSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        comment = Comment.objects.create(incident=incident, user=request.user,
                                         content=data.validated_data["content"])
        return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    # POST /api/incidents/{id}/ai-analysis/
    @action(detail=True, methods=["post"], url_path="ai-analysis",
            throttle_classes=[ScopedRateThrottle])
    def ai_analysis(self, request, pk=None):
        incident = self.get_object()
        try:
            return Response(ai.analyse(incident))
        except ai.AIUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except ai.AIError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    def get_throttles(self):
        if self.action == "ai_analysis":
            self.throttle_scope = "ai"
        return super().get_throttles()


# --- Notifications -----------------------------------------------------------------
class NotificationViewSet(mixins.ListModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    http_method_names = ["get", "patch", "post"]

    def get_queryset(self):
        # Scoped to the caller: another user's notification id simply 404s.
        qs = Notification.objects.filter(user=self.request.user)
        if self.request.query_params.get("unread") == "true":
            qs = qs.filter(is_read=False)
        return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": self.get_queryset().filter(is_read=False).count()})

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        n = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"updated": n})


class HealthView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request):
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return Response({"status": "ok", "ai_configured": ai.is_configured()})
