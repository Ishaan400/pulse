"""Role-level and object-level access rules, kept in one place.

Role summary
  Admin    – manages users and services, sees every incident.
  Manager  – sees every incident, assigns/unassigns engineers, can cancel or close.
  Engineer – creates incidents; sees incidents they created or are assigned to;
             any assigned engineer may move the incident through its lifecycle.
"""
from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Incident

# Lifecycle rules (section 2.2 of the design).
VALID_TRANSITIONS = {
    Incident.Status.OPEN: [Incident.Status.IN_PROGRESS, Incident.Status.CANCELLED],
    Incident.Status.IN_PROGRESS: [Incident.Status.RESOLVED],
    Incident.Status.RESOLVED: [Incident.Status.CLOSED],
}
TERMINAL_STATUSES = {Incident.Status.CLOSED, Incident.Status.CANCELLED}
# Managers may also perform these administrative transitions.
MANAGER_TRANSITIONS = {Incident.Status.CANCELLED, Incident.Status.CLOSED}


def visible_incidents(user):
    qs = Incident.objects.all()
    if user.is_admin_role or user.is_manager:
        return qs
    return qs.filter(Q(created_by=user) | Q(assignments__engineer=user)).distinct()


def is_assigned(user, incident):
    return incident.assignments.filter(engineer=user).exists()


def can_comment(user, incident):
    """Anyone who can see the incident may discuss it."""
    if user.is_admin_role or user.is_manager:
        return True
    return incident.created_by_id == user.id or is_assigned(user, incident)


def can_edit_details(user, incident):
    if incident.status in TERMINAL_STATUSES:
        return False
    if user.is_manager:
        return True
    return user.is_engineer and (incident.created_by_id == user.id or is_assigned(user, incident))


def can_transition(user, incident, new_status):
    if user.is_engineer and is_assigned(user, incident):
        return True
    return user.is_manager and new_status in MANAGER_TRANSITIONS


class IsAdminRole(BasePermission):
    message = "Only admins can do this."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_admin_role)


class IsManager(BasePermission):
    message = "Only managers can assign engineers."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_manager)


class ReadOnlyOrAdmin(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.method in SAFE_METHODS or request.user.is_admin_role


class CanCreateIncident(BasePermission):
    message = "Only engineers can create incidents."

    def has_permission(self, request, view):
        if view.action == "create":
            return request.user.is_engineer
        return True
