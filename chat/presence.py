"""Priority chats are the closest ones, so there both people see a little more of each other:
"Active now" / "Active 5 minutes ago", "typing…" (sent over the live socket, see live/consumers.py)
and "Seen" under your latest message. Nobody else sees any of this, and a message block hides it."""
from datetime import timedelta

from django.utils import timezone
from django.utils.timesince import timesince

from accounts.privacy import messages_blocked

from .models import Conversation

ACTIVE_NOW = timedelta(minutes=2)   # last_seen is written at most once a minute while a tab is open and visible


def shows_presence(conv, blocked=None):
    """Only in an active Priority chat without a message block. Pass `blocked` if it's known."""
    if conv.tier != Conversation.PRIORITY or not conv.is_active:
        return False
    return not (messages_blocked(conv.user1_id, conv.user2_id) if blocked is None else blocked)


def presence(user, now=None):
    """{"now": bool, "text": "Active now" | "Active 5 minutes ago" | ""}"""
    seen = user.last_seen
    if seen is None:
        return {"now": False, "text": ""}
    now = now or timezone.now()
    if now - seen <= ACTIVE_NOW:
        return {"now": True, "text": "Active now"}
    return {"now": False, "text": f"Active {timesince(seen, now, depth=1)} ago"}


def receipt(conv, me):
    """"Seen" or "Sent" when the latest message in the chat is yours, else "" (their reply says it all)."""
    last = (conv.messages.filter(stage=conv.tier, sender__isnull=False).order_by("-id")
            .values_list("id", "sender_id").first())
    if not last or last[1] != me.pk:
        return ""
    their_read = conv.user2_last_read_id if me.pk == conv.user1_id else conv.user1_last_read_id
    return "Seen" if their_read >= last[0] else "Sent"
