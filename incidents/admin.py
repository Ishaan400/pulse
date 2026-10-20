from django.contrib import admin

from .models import (
    Comment,
    Incident,
    IncidentAssignment,
    IncidentHistory,
    Notification,
    Service,
    SLAPolicy,
)


class AssignmentInline(admin.TabularInline):
    model = IncidentAssignment
    fk_name = "incident"
    extra = 0
    readonly_fields = ("engineer", "assigned_by", "assigned_at")
    can_delete = False


class HistoryInline(admin.TabularInline):
    model = IncidentHistory
    extra = 0
    readonly_fields = ("action", "old_value", "new_value", "user", "created_at")
    can_delete = False


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "severity", "status", "service", "sla_deadline", "sla_breached")
    list_filter = ("status", "severity", "sla_breached", "service")
    search_fields = ("title", "description")
    readonly_fields = ("sla_policy", "sla_deadline", "sla_breached", "created_at", "resolved_at",
                       "status", "created_by")
    inlines = [AssignmentInline, HistoryInline]

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Service)
admin.site.register(SLAPolicy)
admin.site.register(Comment)
admin.site.register(Notification)
