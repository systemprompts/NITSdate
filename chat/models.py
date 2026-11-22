import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import ADJECTIVES, ANIMALS


class Conversation(models.Model):
    """A chat between two users.

    tier moves match -> inbox -> priority. Messages remember the tier they were
    sent in (Message.stage), so earlier stages show up as archived history.
    """
    MATCH, INBOX, PRIORITY = "match", "inbox", "priority"
    TIERS = [(MATCH, "Matchmaking"), (INBOX, "Inbox"), (PRIORITY, "Priority")]
    TIER_ORDER = [MATCH, INBOX, PRIORITY]

    ORIGIN_MATCH, ORIGIN_CONNECTION = "match", "connection"
    ORIGINS = [(ORIGIN_MATCH, "Matchmaking"), (ORIGIN_CONNECTION, "Connection request")]

    user1 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    user2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    tier = models.CharField(max_length=10, choices=TIERS, db_index=True)
    origin = models.CharField(max_length=12, choices=ORIGINS)
    is_active = models.BooleanField(default=True, help_text="False once someone leaves a matchmaking chat.")
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    tier_changed_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)
    user1_last_read_id = models.BigIntegerField(default=0)
    user2_last_read_id = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["-last_message_at"]
        indexes = [models.Index(fields=["user1", "user2"])]

    def __str__(self):
        return f"{self.get_tier_display()} #{self.pk}"

    def has_member(self, user):
        return user.pk in (self.user1_id, self.user2_id)

    def other(self, user):
        return self.user2 if user.pk == self.user1_id else self.user1

    def other_id(self, user):
        return self.user2_id if user.pk == self.user1_id else self.user1_id

    def last_read_id(self, user):
        return self.user1_last_read_id if user.pk == self.user1_id else self.user2_last_read_id

    def mark_read(self, user, message_id, tell_other=True):
        """Read up to `message_id`. The update only ever moves forward, even when two tabs race.
        In a Priority chat the other person's open chat is told, so "Sent" turns into "Seen";
        tell_other=False when that can't change anything for them (you just sent the message)."""
        field = "user1_last_read_id" if user.pk == self.user1_id else "user2_last_read_id"
        if message_id <= getattr(self, field):
            return
        setattr(self, field, message_id)
        moved = Conversation.objects.filter(pk=self.pk, **{f"{field}__lt": message_id}).update(**{field: message_id})
        if moved:
            from live.publish import push_badges, push_chat_to
            push_badges(user.pk)   # your other open tabs drop the unread count too
            if tell_other and self.tier == self.PRIORITY:
                push_chat_to(self.other_id(user), self.pk)

    def nickname_for(self, user):
        """Blind name used inside a matchmaking chat so the partner can't look
        the person up by their usual name or alias."""
        digest = hashlib.sha256(f"{self.pk}:{user.pk}:{settings.SECRET_KEY}".encode()).digest()
        return f"{ADJECTIVES[digest[0] % len(ADJECTIVES)]} {ANIMALS[digest[1] % len(ANIMALS)]}"

    @property
    def is_match(self):
        return self.tier == self.MATCH

    def archived_stages(self):
        return self.TIER_ORDER[: self.TIER_ORDER.index(self.tier)]


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                               related_name="+", help_text="Empty for automatic system messages.")
    stage = models.CharField(max_length=10, choices=Conversation.TIERS)
    text = models.TextField(blank=True)
    image = models.ImageField(upload_to="chat/%Y/%m/", blank=True)
    removed = models.BooleanField(default=False, help_text="Removed by a moderator")
    created_at = models.DateTimeField(auto_now_add=True)
    reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # The sender can edit and delete their messages. Nothing is erased: a deleted message keeps its text
    # and photo, and every earlier version of an edited one is kept (MessageEdit), for moderators only.
    # The two people just see "Message deleted" or "Edited".
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    # last edit, delete or moderator removal: open chats fetch messages changed since they last looked
    changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["conversation", "stage", "id"]),
                   models.Index(fields=["conversation", "sender", "stage"]),  # per-person counts
                   models.Index(fields=["conversation", "changed_at"])]

    @property
    def is_system(self):
        return self.sender_id is None

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    @property
    def hidden(self):
        """Its content isn't shown in the chat: deleted by the sender or removed by a moderator."""
        return self.removed or self.deleted_at is not None


class MessageEdit(models.Model):
    """An earlier version of an edited message: `text` is what it said until `replaced_at`.
    Only moderators see these (on reports); the chat only says "Edited"."""
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="edits")
    text = models.TextField(blank=True)
    replaced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class PriorityRequest(models.Model):
    PENDING, ACCEPTED, DECLINED, CANCELLED = "pending", "accepted", "declined", "cancelled"
    STATUSES = [(PENDING, "Pending"), (ACCEPTED, "Accepted"), (DECLINED, "Declined"), (CANCELLED, "Cancelled")]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="priority_requests")
    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
