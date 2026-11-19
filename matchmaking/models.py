from django.conf import settings
from django.db import models
from django.utils import timezone


class MatchSessionQuerySet(models.QuerySet):
    def active(self):
        """Searching and not yet past its expiry. Expiry is a timestamp in the
        database (no timers / in-memory state), so it works on serverless."""
        return self.filter(status=MatchSession.SEARCHING, expires_at__gt=timezone.now())


class MatchSession(models.Model):
    SEARCHING, MATCHED, CANCELLED, EXPIRED = "searching", "matched", "cancelled", "expired"
    STATUSES = [(SEARCHING, "Searching"), (MATCHED, "Matched"), (CANCELLED, "Cancelled"), (EXPIRED, "Expired")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="match_sessions")
    status = models.CharField(max_length=10, choices=STATUSES, default=SEARCHING)
    # filters (gender preference is always taken from the profile's "I like")
    min_age = models.PositiveSmallIntegerField(default=18)
    max_age = models.PositiveSmallIntegerField(default=30)
    looking_for = models.CharField(max_length=200, blank=True, help_text="Comma separated; empty = any")
    personality = models.CharField(max_length=100, blank=True, help_text="Comma separated; empty = any")
    years = models.CharField(max_length=100, blank=True, help_text="Comma separated; empty = any")

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    matched_with = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    conversation = models.ForeignKey("chat.Conversation", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")

    objects = MatchSessionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "expires_at"])]

    def values(self, field):
        return [v for v in getattr(self, field).split(",") if v]

    @property
    def is_searching(self):
        return self.status == self.SEARCHING and self.expires_at > timezone.now()
