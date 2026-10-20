"""Business logic. Views stay thin; every multi-row write here is atomic."""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import (
    Incident,
    IncidentAssignment,
    IncidentHistory,
    Notification,
    SLAPolicy,
)
from .permissions import TERMINAL_STATUSES, VALID_TRANSITIONS, can_transition

logger = logging.getLogger(__name__)
User = get_user_model()

ASSIGNABLE_STATUSES = {Incident.Status.OPEN, Incident.Status.IN_PROGRESS}


def _managers():
    return User.objects.filter(role=User.Role.MANAGER, is_active=True)


def _notify(users, incident, type_, message):
    Notification.objects.bulk_create(
        [Notification(user=u, incident=incident, type=type_, message=message) for u in users],
        ignore_conflicts=True,
    )


# --- Creation -----------------------------------------------------------------
@transaction.atomic
def create_incident(*, user, title, description, severity, service):
    try:
        policy = SLAPolicy.objects.get(severity=severity)
    except SLAPolicy.DoesNotExist:
        raise ValidationError({"severity": f"No SLA policy is configured for {severity}."})

    now = timezone.now()
    incident = Incident.objects.create(
        title=title,
        description=description,
        severity=severity,
        service=service,
        status=Incident.Status.OPEN,
        created_by=user,
        sla_policy=policy,
        sla_deadline=now + policy.resolution_time,
    )
    IncidentHistory.objects.create(
        incident=incident, user=user, action=IncidentHistory.Action.CREATED,
        new_value=Incident.Status.OPEN,
    )
    _notify(
        _managers(), incident, Notification.Type.INCIDENT_CREATED,
        f"New {severity} incident #{incident.pk} needs assignment: {title[:120]}",
    )
    return incident


# --- Assignment (many engineers per incident) ---------------------------------
def _resolve_engineers(engineer_ids):
    ids = list(dict.fromkeys(engineer_ids))  # de-duplicate, keep order
    engineers = list(User.objects.filter(pk__in=ids, is_active=True))
    found = {u.pk for u in engineers}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValidationError({"engineer_ids": f"Unknown or inactive users: {missing}"})
    not_engineers = [u.username for u in engineers if not u.is_engineer]
    if not_engineers:
        raise ValidationError({"engineer_ids": f"Only engineers can be assigned: {not_engineers}"})
    return engineers


@transaction.atomic
def set_assignees(*, incident_id, engineer_ids, actor):
    """Make the incident's assignee set exactly `engineer_ids`.

    Computes the diff against the current IncidentAssignment rows, writes one
    history record per added/removed engineer, and notifies the people affected.
    """
    incident = Incident.objects.select_for_update().filter(pk=incident_id).first()
    if incident is None:
        raise NotFound()
    if incident.status not in ASSIGNABLE_STATUSES:
        raise ValidationError(
            {"detail": f"Assignments cannot change while the incident is {incident.status}."}
        )

    engineers = _resolve_engineers(engineer_ids)
    target = {u.pk: u for u in engineers}
    current = {a.engineer_id: a for a in incident.assignments.select_related("engineer")}

    if incident.status == Incident.Status.IN_PROGRESS and not target:
        raise ValidationError(
            {"engineer_ids": "An in-progress incident must keep at least one engineer."}
        )

    added = [target[i] for i in target if i not in current]
    removed = [current[i] for i in current if i not in target]

    for assignment in removed:
        IncidentHistory.objects.create(
            incident=incident, user=actor, action=IncidentHistory.Action.UNASSIGNED,
            old_value=assignment.engineer.username,
        )
        _notify([assignment.engineer], incident, Notification.Type.UNASSIGNED,
                f"You were unassigned from incident #{incident.pk}.")
        assignment.delete()

    IncidentAssignment.objects.bulk_create(
        [IncidentAssignment(incident=incident, engineer=e, assigned_by=actor) for e in added]
    )
    for engineer in added:
        IncidentHistory.objects.create(
            incident=incident, user=actor, action=IncidentHistory.Action.ASSIGNED,
            new_value=engineer.username,
        )
    _notify(added, incident, Notification.Type.ASSIGNED,
            f"You were assigned to {incident.severity} incident #{incident.pk}: {incident.title[:120]}")

    return incident, added, [a.engineer for a in removed]


# --- Lifecycle ----------------------------------------------------------------
@transaction.atomic
def change_status(*, incident_id, new_status, user):
    # Lock the row so two engineers cannot race the same transition.
    incident = Incident.objects.select_for_update().filter(pk=incident_id).first()
    if incident is None:
        raise NotFound()
    old_status = incident.status

    if new_status == old_status:
        return incident
    if new_status not in VALID_TRANSITIONS.get(old_status, []):
        allowed = VALID_TRANSITIONS.get(old_status, [])
        raise ValidationError({
            "status": f"Cannot move from {old_status} to {new_status}. "
                      f"Allowed: {', '.join(allowed) if allowed else 'none (terminal state)'}."
        })
    if not can_transition(user, incident, new_status):
        raise PermissionDenied(
            "Only an engineer assigned to this incident can make this change"
            + (" (managers may also cancel or close)." if new_status not in TERMINAL_STATUSES else ".")
        )

    incident.status = new_status
    fields = ["status"]
    if new_status == Incident.Status.RESOLVED:
        incident.resolved_at = timezone.now()
        fields.append("resolved_at")
    incident.save(update_fields=fields)
    IncidentHistory.objects.create(
        incident=incident, user=user, action=IncidentHistory.Action.STATUS_CHANGED,
        old_value=old_status, new_value=new_status,
    )
    return incident


# --- SLA monitoring -----------------------------------------------------------
def check_sla_breaches(batch_size=200):
    """Flag overdue unresolved incidents and notify assignees + managers.

    Safe to run concurrently and to retry: rows are claimed with
    SELECT ... FOR UPDATE SKIP LOCKED, the flag is re-checked under the lock, and
    a partial unique index makes duplicate breach notifications impossible.
    """
    total = 0
    managers = list(_managers())
    while True:
        with transaction.atomic():
            batch = list(
                Incident.objects.select_for_update(skip_locked=True)
                .filter(
                    status__in=[Incident.Status.OPEN, Incident.Status.IN_PROGRESS],
                    sla_deadline__lt=timezone.now(),
                    sla_breached=False,
                )
                .order_by("sla_deadline")[:batch_size]
            )
            if not batch:
                break
            for incident in batch:
                incident.sla_breached = True
                incident.save(update_fields=["sla_breached"])
                IncidentHistory.objects.create(
                    incident=incident, action=IncidentHistory.Action.SLA_BREACHED,
                    new_value=incident.sla_deadline.isoformat(),
                )
                recipients = {u.pk: u for u in managers}
                for a in incident.assignments.select_related("engineer"):
                    recipients[a.engineer_id] = a.engineer
                _notify(
                    recipients.values(), incident, Notification.Type.SLA_BREACHED,
                    f"Incident #{incident.pk} ({incident.severity}) has breached its SLA.",
                )
            total += len(batch)
        if len(batch) < batch_size:
            break
    if total:
        logger.info("Marked %s incident(s) as SLA-breached", total)
    return total
