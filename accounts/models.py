from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Every Pulse user. Passwords are hashed by Django's auth framework."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        MANAGER = "MANAGER", "Manager"
        ENGINEER = "ENGINEER", "Engineer"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.ENGINEER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["username"]

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN

    @property
    def is_manager(self):
        return self.role == self.Role.MANAGER

    @property
    def is_engineer(self):
        return self.role == self.Role.ENGINEER

    def __str__(self):
        return f"{self.username} ({self.role})"
