from django.conf import settings
from django.db import models
from django.db.models import Q


class Severity(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class Service(models.Model):
    """An application or system that can have incidents. No status/owner by design."""

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SLAPolicy(models.Model):
    """Time targets per severity. The incident stores its own computed deadline."""

    severity = models.CharField(max_length=16, choices=Severity.choices, unique=True)
    response_time = models.DurationField()
    resolution_time = models.DurationField()

    class Meta:
        verbose_name = "SLA policy"
        verbose_name_plural = "SLA policies"
        ordering = ["resolution_time"]

    def __str__(self):
        return f"{self.severity}: resolve within {self.resolution_time}"


class Incident(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"
        CANCELLED = "CANCELLED", "Cancelled"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="incidents")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_incidents"
    )
    # Many engineers per incident, through an explicit middle table.
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="IncidentAssignment",
        through_fields=("incident", "engineer"),
        related_name="assigned_incidents",
        blank=True,
    )
    sla_policy = models.ForeignKey(SLAPolicy, on_delete=models.PROTECT, related_name="incidents")
    sla_deadline = models.DateTimeField()
    sla_breached = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Supports the periodic SLA breach scan.
            models.Index(
                fields=["sla_deadline"],
                name="incident_sla_open_idx",
                condition=Q(status__in=["OPEN", "IN_PROGRESS"], sla_breached=False),
            ),
            models.Index(fields=["status", "severity"]),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"


class IncidentAssignment(models.Model):
    """Explicit join row: one engineer working one incident.

    Holding the join as a real model lets us record who made the assignment and
    when, and lets us enforce that an engineer appears at most once per incident.
    """

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="assignments")
    engineer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="incident_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assignments_made",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["incident", "engineer"], name="unique_engineer_per_incident"
            )
        ]

    def __str__(self):
        return f"{self.engineer} → #{self.incident_id}"


class IncidentHistory(models.Model):
    class Action(models.TextChoices):
        CREATED = "CREATED", "Created"
        ASSIGNED = "ASSIGNED", "Engineer assigned"
        UNASSIGNED = "UNASSIGNED", "Engineer unassigned"
        STATUS_CHANGED = "STATUS_CHANGED", "Status changed"
        SLA_BREACHED = "SLA_BREACHED", "SLA breached"

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="history")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )  # null for system events (e.g. the SLA checker)
    action = models.CharField(max_length=32, choices=Action.choices)
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "incident history"


class Comment(models.Model):
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class Notification(models.Model):
    class Type(models.TextChoices):
        INCIDENT_CREATED = "INCIDENT_CREATED", "Incident created"
        ASSIGNED = "ASSIGNED", "Assigned"
        UNASSIGNED = "UNASSIGNED", "Unassigned"
        SLA_BREACHED = "SLA_BREACHED", "SLA breached"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=32, choices=Type.choices)
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "is_read"])]
        constraints = [
            # A breach is notified once per user per incident, even if the task retries.
            models.UniqueConstraint(
                fields=["user", "incident"],
                condition=Q(type="SLA_BREACHED"),
                name="one_breach_notification_per_user",
            )
        ]
