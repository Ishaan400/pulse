from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from incidents import services
from incidents.models import Incident, IncidentAssignment, IncidentHistory, Notification

pytestmark = pytest.mark.django_db


# --- creation & SLA -------------------------------------------------------------
def test_engineer_creates_incident_and_server_owns_protected_fields(client_for, eng1, service, manager):
    before = timezone.now()
    r = client_for(eng1).post("/api/incidents/", {
        "title": "Payment API failing", "description": "HTTP 500", "severity": "HIGH",
        "service": service.pk,
        # attempts to set protected fields are ignored
        "status": "CLOSED", "created_by": manager.pk, "sla_breached": True,
        "sla_deadline": "2000-01-01T00:00:00Z",
    }, format="json")
    assert r.status_code == 201, r.data
    inc = Incident.objects.get(pk=r.data["id"])
    assert inc.status == "OPEN"
    assert inc.created_by == eng1
    assert inc.sla_breached is False
    assert inc.sla_policy.severity == "HIGH"
    assert timedelta(hours=2) - timedelta(seconds=5) < inc.sla_deadline - before < timedelta(hours=2, seconds=5)
    assert inc.history.filter(action="CREATED").count() == 1
    assert Notification.objects.filter(user=manager, type="INCIDENT_CREATED", incident=inc).exists()


@pytest.mark.parametrize("role", ["manager", "admin"])
def test_only_engineers_create(client_for, request, service, role):
    r = client_for(request.getfixturevalue(role)).post(
        "/api/incidents/", {"title": "x", "severity": "LOW", "service": service.pk}, format="json")
    assert r.status_code == 403


# --- multi-engineer assignment ---------------------------------------------------
def test_manager_assigns_multiple_engineers(client_for, manager, incident, eng1, eng2):
    r = client_for(manager).patch(f"/api/incidents/{incident.pk}/assign/",
                                  {"engineer_ids": [eng1.pk, eng2.pk]}, format="json")
    assert r.status_code == 200, r.data
    assert sorted(a["engineer"]["username"] for a in r.data["assignments"]) == ["eli", "sam"]
    assert IncidentAssignment.objects.filter(incident=incident).count() == 2
    assert all(a.assigned_by == manager for a in incident.assignments.all())
    assert incident.history.filter(action="ASSIGNED").count() == 2
    assert Notification.objects.filter(type="ASSIGNED", user__in=[eng1, eng2]).count() == 2


def test_reassignment_diffs_the_set(client_for, manager, incident, eng1, eng2, eng3):
    c = client_for(manager)
    c.patch(f"/api/incidents/{incident.pk}/assign/", {"engineer_ids": [eng1.pk, eng2.pk]}, format="json")
    r = c.patch(f"/api/incidents/{incident.pk}/assign/", {"engineer_ids": [eng2.pk, eng3.pk]}, format="json")
    assert r.status_code == 200
    assert r.data["added"] == ["rio"] and r.data["removed"] == ["eli"]
    assert set(incident.assignees.values_list("username", flat=True)) == {"sam", "rio"}
    # sam was kept, so sam is notified only once in total
    assert Notification.objects.filter(type="ASSIGNED", user=eng2).count() == 1
    assert Notification.objects.filter(type="UNASSIGNED", user=eng1).count() == 1
    assert incident.history.filter(action="UNASSIGNED", old_value="eli").exists()


def test_duplicate_ids_are_collapsed(client_for, manager, incident, eng1):
    r = client_for(manager).patch(f"/api/incidents/{incident.pk}/assign/",
                                  {"engineer_ids": [eng1.pk, eng1.pk]}, format="json")
    assert r.status_code == 200
    assert incident.assignments.count() == 1


def test_cannot_assign_non_engineer_or_unknown(client_for, manager, incident, admin):
    c = client_for(manager)
    assert c.patch(f"/api/incidents/{incident.pk}/assign/", {"engineer_ids": [admin.pk]},
                   format="json").status_code == 400
    assert c.patch(f"/api/incidents/{incident.pk}/assign/", {"engineer_ids": [99999]},
                   format="json").status_code == 400
    assert incident.assignments.count() == 0


@pytest.mark.parametrize("role", ["eng1", "admin"])
def test_only_managers_assign(client_for, request, incident, eng2, role):
    r = client_for(request.getfixturevalue(role)).patch(
        f"/api/incidents/{incident.pk}/assign/", {"engineer_ids": [eng2.pk]}, format="json")
    assert r.status_code == 403


def test_in_progress_incident_keeps_at_least_one_engineer(client_for, manager, incident, eng1):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    services.change_status(incident_id=incident.pk, new_status="IN_PROGRESS", user=eng1)
    r = client_for(manager).patch(f"/api/incidents/{incident.pk}/assign/", {"engineer_ids": []}, format="json")
    assert r.status_code == 400


def test_no_assignment_changes_after_resolution(client_for, manager, incident, eng1, eng2):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    services.change_status(incident_id=incident.pk, new_status="IN_PROGRESS", user=eng1)
    services.change_status(incident_id=incident.pk, new_status="RESOLVED", user=eng1)
    r = client_for(manager).patch(f"/api/incidents/{incident.pk}/assign/", {"engineer_ids": [eng2.pk]}, format="json")
    assert r.status_code == 400


# --- lifecycle -------------------------------------------------------------------------
def test_any_assigned_engineer_can_drive_lifecycle(client_for, manager, incident, eng1, eng2):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk, eng2.pk], actor=manager)
    assert client_for(eng1).patch(f"/api/incidents/{incident.pk}/", {"status": "IN_PROGRESS"},
                                  format="json").status_code == 200
    r = client_for(eng2).patch(f"/api/incidents/{incident.pk}/", {"status": "RESOLVED"}, format="json")
    assert r.status_code == 200 and r.data["resolved_at"]
    hist = list(incident.history.filter(action="STATUS_CHANGED").values_list("old_value", "new_value", "user__username"))
    assert hist == [("OPEN", "IN_PROGRESS", "eli"), ("IN_PROGRESS", "RESOLVED", "sam")]


def test_unassigned_engineer_cannot_change_status(client_for, manager, incident, eng1, eng2):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    # eng2 cannot even see it (not creator, not assigned) -> 404
    assert client_for(eng2).patch(f"/api/incidents/{incident.pk}/", {"status": "IN_PROGRESS"},
                                  format="json").status_code == 404
    # eng1 created it in the fixture; create one by eng2 and assign eng1 only
    other = services.create_incident(user=eng2, title="t", description="", severity="LOW",
                                     service=incident.service)
    services.set_assignees(incident_id=other.pk, engineer_ids=[eng1.pk], actor=manager)
    assert client_for(eng2).patch(f"/api/incidents/{other.pk}/", {"status": "IN_PROGRESS"},
                                  format="json").status_code == 403


@pytest.mark.parametrize("path", [["RESOLVED"], ["CLOSED"], ["IN_PROGRESS", "OPEN"], ["IN_PROGRESS", "CANCELLED"]])
def test_invalid_transitions_rejected(client_for, manager, incident, eng1, path):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    c = client_for(eng1)
    for i, s in enumerate(path):
        r = c.patch(f"/api/incidents/{incident.pk}/", {"status": s}, format="json")
        if i == len(path) - 1:
            assert r.status_code == 400
    assert Incident.objects.get(pk=incident.pk).status == ("IN_PROGRESS" if len(path) == 2 else "OPEN")


def test_terminal_states_are_final(manager, incident, eng1):
    services.change_status(incident_id=incident.pk, new_status="CANCELLED", user=manager)
    with pytest.raises(Exception):
        services.change_status(incident_id=incident.pk, new_status="OPEN", user=manager)


def test_manager_can_cancel_and_close_but_not_work(client_for, manager, incident, eng1):
    c = client_for(manager)
    assert c.patch(f"/api/incidents/{incident.pk}/", {"status": "IN_PROGRESS"}, format="json").status_code == 403
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    services.change_status(incident_id=incident.pk, new_status="IN_PROGRESS", user=eng1)
    services.change_status(incident_id=incident.pk, new_status="RESOLVED", user=eng1)
    assert c.patch(f"/api/incidents/{incident.pk}/", {"status": "CLOSED"}, format="json").status_code == 200


def test_patch_rejects_protected_fields(client_for, manager, incident, eng1):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    r = client_for(eng1).patch(f"/api/incidents/{incident.pk}/", {"sla_breached": True, "severity": "LOW"},
                               format="json")
    assert r.status_code == 400
    assert "sla_breached" in r.data and "severity" in r.data


def test_incidents_cannot_be_deleted(client_for, admin, incident):
    assert client_for(admin).delete(f"/api/incidents/{incident.pk}/").status_code == 405


# --- visibility, comments, history ---------------------------------------------------
def test_engineer_visibility(client_for, manager, incident, eng1, eng2):
    assert client_for(eng2).get("/api/incidents/").data["count"] == 0
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk, eng2.pk], actor=manager)
    assert client_for(eng2).get("/api/incidents/").data["count"] == 1  # no duplicates from the join
    assert client_for(manager).get("/api/incidents/?mine=true").data["count"] == 0
    assert client_for(eng2).get("/api/incidents/?mine=true").data["count"] == 1


def test_comments_author_from_session(client_for, manager, incident, eng1, eng2):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng2.pk], actor=manager)
    r = client_for(eng2).post(f"/api/incidents/{incident.pk}/comments/",
                              {"content": "Rollback completed.", "user": eng1.pk}, format="json")
    assert r.status_code == 201 and r.data["user"]["username"] == "sam"
    assert len(client_for(eng1).get(f"/api/incidents/{incident.pk}/comments/").data) == 1
    # comments do not add workflow history
    assert not incident.history.filter(action__icontains="COMMENT").exists()


def test_outsider_cannot_comment(client_for, incident, eng3):
    r = client_for(eng3).post(f"/api/incidents/{incident.pk}/comments/", {"content": "hi"}, format="json")
    assert r.status_code == 404


def test_history_endpoint(client_for, manager, incident, eng1):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    actions = [h["action"] for h in client_for(manager).get(f"/api/incidents/{incident.pk}/history/").data]
    assert actions == ["CREATED", "ASSIGNED"]


# --- SLA breach --------------------------------------------------------------------
def test_sla_breach_notifies_all_assignees_and_managers_once(manager, incident, eng1, eng2):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk, eng2.pk], actor=manager)
    Incident.objects.filter(pk=incident.pk).update(sla_deadline=timezone.now() - timedelta(minutes=1))
    assert services.check_sla_breaches() == 1
    assert services.check_sla_breaches() == 0  # idempotent
    incident.refresh_from_db()
    assert incident.sla_breached
    recipients = set(Notification.objects.filter(type="SLA_BREACHED").values_list("user__username", flat=True))
    assert recipients == {"eli", "sam", "maya"}
    assert Notification.objects.filter(type="SLA_BREACHED").count() == 3
    assert IncidentHistory.objects.filter(incident=incident, action="SLA_BREACHED").count() == 1


def test_resolved_incidents_do_not_breach(manager, incident, eng1):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    services.change_status(incident_id=incident.pk, new_status="IN_PROGRESS", user=eng1)
    services.change_status(incident_id=incident.pk, new_status="RESOLVED", user=eng1)
    Incident.objects.filter(pk=incident.pk).update(sla_deadline=timezone.now() - timedelta(minutes=1))
    assert services.check_sla_breaches() == 0


def test_breach_notification_unique_constraint(manager, incident):
    from django.db import IntegrityError, transaction
    Notification.objects.create(user=manager, incident=incident, type="SLA_BREACHED", message="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.create(user=manager, incident=incident, type="SLA_BREACHED", message="y")


# --- notifications -----------------------------------------------------------------
def test_notifications_are_private(client_for, manager, incident, eng1):
    services.set_assignees(incident_id=incident.pk, engineer_ids=[eng1.pk], actor=manager)
    mine = client_for(eng1).get("/api/notifications/").data["results"]
    assert len(mine) == 1
    manager_note = Notification.objects.filter(user=manager).first()
    assert client_for(eng1).patch(f"/api/notifications/{manager_note.pk}/", {"is_read": True},
                                  format="json").status_code == 404
    r = client_for(eng1).patch(f"/api/notifications/{mine[0]['id']}/", {"is_read": True, "message": "hacked"},
                               format="json")
    assert r.status_code == 200 and r.data["is_read"] and r.data["message"] != "hacked"
    assert client_for(eng1).get("/api/notifications/unread-count/").data["unread"] == 0


# --- auth, users, AI, metrics ----------------------------------------------------
def test_session_login_and_csrf(eng1):
    c = APIClient(enforce_csrf_checks=True)
    assert c.get("/api/auth/me/").status_code == 403
    token = c.get("/api/auth/csrf/").data["csrfToken"]
    r = c.post("/api/auth/login/", {"username": "eli", "password": "pw-Strong-123"},
               format="json", HTTP_X_CSRFTOKEN=token)
    assert r.status_code == 200 and "sessionid" in c.cookies
    assert c.get("/api/auth/me/").data["role"] == "ENGINEER"
    # unsafe request without CSRF token is rejected for a session-authenticated user
    assert c.post("/api/auth/logout/").status_code == 403


def test_bad_login(db, eng1):
    r = APIClient().post("/api/auth/login/", {"username": "eli", "password": "nope"}, format="json")
    assert r.status_code == 400


def test_admin_manages_users_and_services(client_for, admin, manager):
    c = client_for(admin)
    r = c.post("/api/users/", {"username": "new", "email": "n@t.local", "role": "ENGINEER",
                                "password": "Another-Strong-9"}, format="json")
    assert r.status_code == 201 and "password" not in r.data
    assert c.post("/api/services/", {"name": "Search"}, format="json").status_code == 201
    m = client_for(manager)
    assert m.get("/api/users/").status_code == 403
    assert m.post("/api/services/", {"name": "X"}, format="json").status_code == 403
    assert m.get("/api/services/").status_code == 200


def test_ai_analysis_without_key_returns_503(client_for, incident, eng1, settings):
    settings.GROQ_API_KEY = ""
    r = client_for(eng1).post(f"/api/incidents/{incident.pk}/ai-analysis/")
    assert r.status_code == 503


def test_ai_analysis_with_key_calls_provider(client_for, incident, eng1, settings, monkeypatch):
    settings.GROQ_API_KEY = "test-key"
    captured = {}

    class Message:
        content = "**Summary** looks like a bad deploy"

    class Choice:
        message = Message()

    class Resp:
        choices = [Choice()]
        model = "groq-test"

    class FakeCompletions:
        def create(self, **kw):
            captured.update(kw)
            return Resp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kw):
            self.chat = FakeChat()

    import groq
    monkeypatch.setattr(groq, "Groq", FakeClient)
    r = client_for(eng1).post(f"/api/incidents/{incident.pk}/ai-analysis/")
    assert r.status_code == 200 and "bad deploy" in r.data["analysis"]
    assert "500s on checkout" in captured["messages"][1]["content"]


def test_metrics_endpoint(client, incident, settings):
    settings.DEBUG = False
    body = client.get("/metrics", REMOTE_ADDR="172.18.0.5").content.decode()
    assert 'pulse_incidents{severity="HIGH",status="OPEN"} 1.0' in body
    assert client.get("/metrics", REMOTE_ADDR="8.8.8.8").status_code == 404
    assert client.get("/metrics", REMOTE_ADDR="172.18.0.1", HTTP_X_FORWARDED_FOR="8.8.8.8").status_code == 404
