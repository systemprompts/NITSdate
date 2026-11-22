"""How recently the other person was on nitsdate, shown in active chats without a message block.
The closer the chat, the finer it gets:

  Priority     to the minute  "Active now" (last 2 minutes), "Active 5 minutes ago"
  Inbox        to the hour    "Active now" (last hour), "Active 3 hours ago", "Active 2 days ago"
  Matchmaking  to the day     "Active today", "Active yesterday", "Active 4 days ago"

Priority chats also get "typing…" (sent over the live socket, see live/consumers.py) and "Seen" under
your latest message. Nobody else sees any of this, and a message block hides it."""
from datetime import timedelta

from django.utils import timezone
from django.utils.timesince import timesince

from accounts.privacy import messages_blocked

from .models import Conversation

ACTIVE_NOW = timedelta(minutes=2)   # last_seen is written at most once a minute while a tab is open and visible
MAX_DAYS = 30                       # longer ago than this: "Active over a month ago"


def shows_presence(conv, blocked=None):
    """In any active chat without a message block. Pass `blocked` if it's known."""
    if not conv.is_active:
        return False
    return not (messages_blocked(conv.user1_id, conv.user2_id) if blocked is None else blocked)


def shows_receipts(conv, blocked=None):
    """"typing…" and "Seen": active Priority chats without a message block only."""
    return conv.tier == Conversation.PRIORITY and shows_presence(conv, blocked)


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _days(days):
    if days > MAX_DAYS:
        return "Active over a month ago"
    return f"Active {_plural(days, 'day')} ago"


def presence(user, tier, now=None):
    """{"now": bool, "text": "..."} for a chat of this tier; "now" (the green dot) goes with "Active now".
    The text is "" when they haven't been seen since this started being recorded."""
    seen = user.last_seen
    if seen is None:
        return {"now": False, "text": ""}
    now = now or timezone.now()
    ago = max(now - seen, timedelta(0))
    if tier == Conversation.PRIORITY:
        if ago <= ACTIVE_NOW:
            return {"now": True, "text": "Active now"}
        return {"now": False, "text": f"Active {timesince(seen, now, depth=1)} ago"}
    if tier == Conversation.INBOX:
        hours = int(ago.total_seconds() // 3600)
        if hours < 1:                       # 0 to 59:59 minutes
            return {"now": True, "text": "Active now"}
        if hours < 24:
            text = f"Active {_plural(hours, 'hour')} ago"
        else:
            text = _days(hours // 24)
        return {"now": False, "text": text}
    # matchmaking: calendar days in Indian time, so "today" starts at midnight
    days = (timezone.localdate(now) - timezone.localdate(min(seen, now))).days
    text = "Active today" if days == 0 else "Active yesterday" if days == 1 else _days(days)
    return {"now": False, "text": text}


def receipt(conv, me):
    """"Seen" or "Sent" when the latest message in the chat is yours, else "" (their reply says it all)."""
    last = (conv.messages.filter(stage=conv.tier, sender__isnull=False).order_by("-id")
            .values_list("id", "sender_id").first())
    if not last or last[1] != me.pk:
        return ""
    their_read = conv.user2_last_read_id if me.pk == conv.user1_id else conv.user1_last_read_id
    return "Seen" if their_read >= last[0] else "Sent"
