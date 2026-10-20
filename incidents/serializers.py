from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import (
    Comment,
    Incident,
    IncidentAssignment,
    IncidentHistory,
    Notification,
    Service,
    SLAPolicy,
)

User = get_user_model()


class UserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "role"]


class UserSerializer(serializers.ModelSerializer):
    """Admin-facing user management. Password is write-only and hashed."""

    password = serializers.CharField(write_only=True, required=False, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "role",
                  "is_active", "created_at", "password"]
        read_only_fields = ["id", "created_at"]

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "A password is required."})
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "name", "description", "created_at"]
        read_only_fields = ["id", "created_at"]


class SLAPolicySerializer(serializers.ModelSerializer):
    response_minutes = serializers.SerializerMethodField()
    resolution_minutes = serializers.SerializerMethodField()

    class Meta:
        model = SLAPolicy
        fields = ["id", "severity", "response_time", "resolution_time",
                  "response_minutes", "resolution_minutes"]

    def get_response_minutes(self, obj):
        return int(obj.response_time.total_seconds() // 60)

    def get_resolution_minutes(self, obj):
        return int(obj.resolution_time.total_seconds() // 60)


class AssignmentSerializer(serializers.ModelSerializer):
    engineer = UserSummarySerializer(read_only=True)
    assigned_by = UserSummarySerializer(read_only=True)

    class Meta:
        model = IncidentAssignment
        fields = ["id", "engineer", "assigned_by", "assigned_at"]


class IncidentSerializer(serializers.ModelSerializer):
    """Read representation. Protected fields are always server-owned."""

    service = ServiceSerializer(read_only=True)
    created_by = UserSummarySerializer(read_only=True)
    assignments = AssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = Incident
        fields = ["id", "title", "description", "severity", "status", "service",
                  "created_by", "assignments", "sla_policy", "sla_deadline",
                  "sla_breached", "created_at", "resolved_at"]
        read_only_fields = fields


class IncidentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(allow_blank=True, required=False, default="")
    severity = serializers.ChoiceField(choices=Incident._meta.get_field("severity").choices)
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.all())


class IncidentUpdateSerializer(serializers.Serializer):
    """PATCH body. Only these fields are client-editable; anything else is rejected."""

    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    status = serializers.ChoiceField(choices=Incident.Status.choices, required=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {f: "This field cannot be changed through this endpoint." for f in sorted(unknown)}
            )
        if not attrs:
            raise serializers.ValidationError("Provide at least one of: title, description, status.")
        return attrs


class AssignSerializer(serializers.Serializer):
    engineer_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=True)


class HistorySerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)

    class Meta:
        model = IncidentHistory
        fields = ["id", "action", "old_value", "new_value", "user", "created_at"]


class CommentSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)

    class Meta:
        model = Comment
        fields = ["id", "incident", "user", "content", "created_at"]
        read_only_fields = ["id", "incident", "user", "created_at"]

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("Comment cannot be empty.")
        return value


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "incident", "type", "message", "is_read", "created_at"]
        read_only_fields = ["id", "incident", "type", "message", "created_at"]


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"})
