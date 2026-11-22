from django.conf import settings
from django.db import models


class Notification(models.Model):
    """Something happened to your post or comment. Upvotes never say who voted: the actor is kept
    only so one person toggling a vote can't add up to many notifications."""

    POST_UPVOTE, COMMENT_UPVOTE, COMMENT, REPLY = "post_upvote", "comment_upvote", "comment", "reply"
    KINDS = [(POST_UPVOTE, "Upvote on your post"), (COMMENT_UPVOTE, "Upvote on your comment"),
             (COMMENT, "Comment on your post"), (REPLY, "Reply to your comment")]
    UPVOTES = (POST_UPVOTE, COMMENT_UPVOTE)

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=20, choices=KINDS)
    post = models.ForeignKey("feed.Post", on_delete=models.CASCADE, related_name="+")
    comment = models.ForeignKey("feed.Comment", null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                                help_text="The upvoted comment, or the new comment / reply")
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "read_at"]), models.Index(fields=["recipient", "-created_at"])]


class PushSubscription(models.Model):
    """A browser or phone that said yes to push notifications (Web Push). It's told about new messages
    and matches, even with nitsdate closed. Signing out removes it (see notifications/signals.py)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.TextField()                          # the push service's URL for this browser
    key = models.CharField(max_length=64, unique=True)     # sha256 of the endpoint (too long to index itself)
    p256dh = models.CharField(max_length=200)              # the browser's public key: payloads are encrypted to it
    auth = models.CharField(max_length=100)
    session_key = models.CharField(max_length=40, blank=True, db_index=True)   # the sign-in that added it
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
