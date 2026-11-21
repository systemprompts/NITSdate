"""Tell open pages that something changed.

The socket only carries "look again" signals and the nav badge counts; pages fetch everything
else through the normal, permission-checked views, so it never shows more than they do.

Sending is best-effort and happens once the database change has committed. If Redis is down the
request still succeeds, and pages catch up on their own timers.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.db.models import Q

log = logging.getLogger(__name__)

MODS = "mods"


def user_group(user_id):
    return f"user.{user_id}"


def _send_now(group, message):
    # "publish" has a short Redis timeout, so a stuck Redis can't hold up a page; with the
    # in-memory layer (tests, local dev) there's only "default"
    alias = "publish" if "publish" in settings.CHANNEL_LAYERS else "default"
    try:
        layer = get_channel_layer(alias)
        if layer is not None:
            async_to_sync(layer.group_send)(group, message)
    except Exception:
        log.warning("live: couldn't publish to %s", group, exc_info=True)


def _send(group, message):
    transaction.on_commit(lambda: _send_now(group, message))


def push_badges(*user_ids):
    """Their nav badges (unread messages, requests, notifications, searching dot) may have changed."""
    for user_id in {u for u in user_ids if u}:
        _send(user_group(user_id), {"type": "live.badges"})


def push_conversation(conv):
    """Something in this chat changed: an open chat page fetches what's new, and both people's
    badges and Messages list update."""
    for user_id in (conv.user1_id, conv.user2_id):
        if user_id:
            _send(user_group(user_id), {"type": "live.chat", "conv": conv.pk})


def push_chat_to(user_id, conversation_id):
    """Only `user_id`'s open copy of this chat needs to look again (e.g. their message was seen)."""
    _send(user_group(user_id), {"type": "live.chat", "conv": conversation_id})


def push_pair(a_id, b_id):
    """A block between two people changed: every chat between them, and both people's badges."""
    from chat.models import Conversation

    pair = Q(user1_id=a_id, user2_id=b_id) | Q(user1_id=b_id, user2_id=a_id)
    for conv in Conversation.objects.filter(pair).only("id", "user1_id", "user2_id"):
        push_conversation(conv)
    push_badges(a_id, b_id)


def push_mods():
    """The number of open reports changed."""
    _send(MODS, {"type": "live.badges"})


def push_match(user_id, conversation_id):
    _send(user_group(user_id), {"type": "live.match", "conv": conversation_id})


def close_user(user_id):
    """The account is gone: its open pages are disconnected."""
    _send(user_group(user_id), {"type": "live.close"})
