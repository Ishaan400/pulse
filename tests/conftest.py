import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from incidents import services
from incidents.models import Service

User = get_user_model()


@pytest.fixture(autouse=True)
def _no_throttle(settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": []}


def make_user(username, role):
    return User.objects.create_user(username=username, email=f"{username}@t.local",
                                    password="pw-Strong-123", role=role)


@pytest.fixture
def admin(db):
    return make_user("admin", "ADMIN")


@pytest.fixture
def manager(db):
    return make_user("maya", "MANAGER")


@pytest.fixture
def eng1(db):
    return make_user("eli", "ENGINEER")


@pytest.fixture
def eng2(db):
    return make_user("sam", "ENGINEER")


@pytest.fixture
def eng3(db):
    return make_user("rio", "ENGINEER")


@pytest.fixture
def service(db):
    return Service.objects.create(name="Payment API")


@pytest.fixture
def client_for():
    def _make(user):
        c = APIClient()
        c.force_authenticate(user)
        return c
    return _make


@pytest.fixture
def incident(eng1, service, manager):
    return services.create_incident(user=eng1, title="500s on checkout", description="",
                                    severity="HIGH", service=service)
