"""Web Push: phones and browsers that said yes get a notification for a new message or a match, even
with nitsdate closed.

What's sent is encrypted for that browser, so the push service in between (Google, Mozilla, Apple or
Microsoft, depending on the browser) can't read it. It says who wrote (as the reader would see them:
a blind nickname in a matchmaking chat), never what they wrote.

Sending happens after the database change commits, on a small thread pool, so a slow push service
never holds up a page. Off unless VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are set.
"""
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

log = logging.getLogger(__name__)

# push services of the browsers people use; nothing else is ever sent to
# (Chrome, Edge on Android, Samsung, Brave, Opera: Google; Firefox: Mozilla; Safari: Apple; Edge on Windows: Microsoft)
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com",
              "notify.windows.com")
MAX_PER_USER = 10                      # devices per person; the oldest go first
QUIET_FOR = timedelta(minutes=10)      # more messages in a row while unread: one notification, not one each
TTL = 6 * 3600                         # a phone that's off gets it within 6 hours, or not at all

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push")


def enabled():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def endpoint_key(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()


def endpoint_allowed(endpoint):
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


# ---- what gets sent ------------------------------------------------------------------------------

def message_sent(message):
    """Someone wrote in a chat: the other person hears about it."""
    if enabled() and message.sender_id:
        _later(_message_job, message.pk)


def match_found(user_id, conversation_id):
    if enabled():
        _later(_send_to, user_id, {
            "title": "It's a match! 💘",
            "body": "Someone who fits your search is waiting. Say hi.",
            "url": f"/messages/{conversation_id}/", "tag": f"match-{conversation_id}", "quiet_if": "site",
        })


def _message_job(message_id):
    from accounts.privacy import display_name
    from chat.models import Message

    msg = Message.objects.select_related("conversation", "sender__profile").filter(pk=message_id).first()
    if msg is None or msg.sender is None or msg.hidden:
        return
    conv = msg.conversation
    to_id = conv.user2_id if msg.sender_id == conv.user1_id else conv.user1_id
    from .models import PushSubscription
    if not PushSubscription.objects.filter(user_id=to_id).exists():
        return
    # they haven't read the last one yet and it's recent: they already have a notification for this chat
    read_upto = conv.user1_last_read_id if to_id == conv.user1_id else conv.user2_last_read_id
    earlier = (conv.messages.filter(sender_id=msg.sender_id, id__gt=read_upto, id__lt=msg.pk)
               .order_by("-id").values_list("created_at", flat=True).first())
    if earlier and msg.created_at - earlier < QUIET_FOR:
        return
    from accounts.models import User
    reader = User.objects.filter(pk=to_id, is_active=True).first()
    if reader is None:
        return
    name = conv.nickname_for(msg.sender) if conv.is_match else display_name(msg.sender, reader)
    _send_to(to_id, {
        "title": name,
        "body": ("Sent you a GIF" if msg.is_gif else "Sent you a photo") if msg.image and not msg.text
                else "Sent you a message",
        "url": f"/messages/{conv.pk}/", "tag": f"chat-{conv.pk}", "quiet_if": "page",
    })


# ---- delivery ------------------------------------------------------------------------------------

def _later(fn, *args):
    """Run `fn` on the push threads once the current transaction commits."""
    def job():
        close_old_connections()
        try:
            fn(*args)
        except Exception:
            log.exception("push: job failed")
        finally:
            connection.close()   # this thread's own database connection
    transaction.on_commit(lambda: _pool.submit(job))


def _send_to(user_id, payload):
    from .models import PushSubscription
    data = json.dumps(payload)
    for sub in PushSubscription.objects.filter(user_id=user_id)[:MAX_PER_USER]:
        _deliver(sub, data)


def _deliver(sub, data):
    from pywebpush import WebPushException, webpush

    from .models import PushSubscription
    try:
        webpush(subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=data, vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},   # a fresh dict: webpush adds to it
                ttl=TTL, headers={"Urgency": "high"}, timeout=10)
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):          # the browser unsubscribed or the subscription expired
            PushSubscription.objects.filter(pk=sub.pk).delete()
        else:                             # never log the endpoint: it works like an address for that phone
            log.warning("push: delivery failed (status %s)", status)
        return False
    except Exception as exc:
        log.warning("push: delivery failed (%s)", type(exc).__name__)
        return False
    PushSubscription.objects.filter(pk=sub.pk).update(last_sent_at=timezone.now())
    return True
