from django.conf import settings
from django.db import models

from accounts.campuses import CAMPUS_CHOICES, default_campus


class ModeratorGrant(models.Model):
    """Emails an admin gave moderator access. Works before the person has signed up."""
    email = models.EmailField(unique=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["email"]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class AllowedEmail(models.Model):
    """Emails moderators let in even though they aren't college addresses of an open NIT
    (settings.OPEN_CAMPUSES). `campus` is the NIT their account belongs to."""
    email = models.EmailField(unique=True)
    note = models.CharField(max_length=200, blank=True)
    campus = models.CharField(max_length=20, choices=CAMPUS_CHOICES, default=default_campus)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["email"]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class Report(models.Model):
    POST, COMMENT, MESSAGE, PROFILE = "post", "comment", "message", "profile"
    KINDS = [(POST, "Post"), (COMMENT, "Comment"), (MESSAGE, "Message"), (PROFILE, "Profile")]
    REASONS = [
        ("harassment", "Harassment or bullying"),
        ("sexual", "Sexual or inappropriate content"),
        ("spam", "Spam or scam"),
        ("fake", "Fake profile or impersonation"),
        ("underage", "Looks under 18"),
        ("hate", "Hate speech"),
        ("other", "Something else"),
    ]
    OPEN, RESOLVED, DISMISSED = "open", "resolved", "dismissed"
    STATUSES = [(OPEN, "Open"), (RESOLVED, "Action taken"), (DISMISSED, "Dismissed")]

    kind = models.CharField(max_length=10, choices=KINDS)
    reason = models.CharField(max_length=12, choices=REASONS)
    details = models.TextField(blank=True)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="reports_filed")
    reported_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="reports_received")
    post = models.ForeignKey("feed.Post", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    comment = models.ForeignKey("feed.Comment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    message = models.ForeignKey("chat.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    conversation = models.ForeignKey("chat.Conversation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    snapshot = models.TextField(blank=True, help_text="Copy of the content when it was reported.")
    status = models.CharField(max_length=10, choices=STATUSES, default=OPEN, db_index=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class Restriction(models.Model):
    """Limits a moderator put on one account. Missing row = no limits."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="restriction")
    request_limit = models.PositiveSmallIntegerField(null=True, blank=True,
                                                     help_text="Profile connection requests per day; empty = normal limit")
    no_matchmaking = models.BooleanField(default=False)
    no_posting = models.BooleanField(default=False)
    no_interaction = models.BooleanField(default=False, help_text="No voting or commenting")
    no_messaging = models.BooleanField(default=False)
    profile_hidden = models.BooleanField(default=False, help_text="Profile hidden from everyone, connections too")
    note = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    FLAGS = [
        ("no_matchmaking", "No matchmaking"),
        ("no_posting", "No new posts"),
        ("no_interaction", "No voting or commenting"),
        ("no_messaging", "No sending messages"),
        ("profile_hidden", "Profile hidden from everyone (connections too)"),
    ]

    @property
    def is_active(self):
        return self.request_limit is not None or any(getattr(self, f) for f, _ in self.FLAGS)


class ModAction(models.Model):
    """Audit log of everything moderators do."""
    mod = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    report = models.ForeignKey(Report, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions")
    action = models.CharField(max_length=40)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
