"""Connection / priority workflow and conversation helpers."""
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from accounts.models import Connection, ConnectionRequest, pair_ids
from accounts.privacy import (AGE_GROUP_MESSAGE, any_block, forget_connections, messages_blocked, same_age_group,
                              visible_block)

from .models import Conversation, Message, PriorityRequest

TEXT_MATCHED = ("It's a match! 🎉 You can't see each other's profiles yet — just talk. "
                "Once you've each sent {n} messages, a Connect button appears.")
TEXT_INBOX = "Thank you for so much love, let's bind strongly 💞"
TEXT_PRIORITY = "You're each other's Priority now ⭐ Thank you for so much love, let's bind even stronger!"


class WorkflowError(Exception):
    pass


def pair_q(a, b, prefix=""):
    u1, u2 = pair_ids(a, b)
    return Q(**{f"{prefix}user1_id": u1, f"{prefix}user2_id": u2})


def member_q(user):
    return Q(user1=user) | Q(user2=user)


def system_message(conv, text):
    msg = Message.objects.create(conversation=conv, sender=None, stage=conv.tier, text=text)
    Conversation.objects.filter(pk=conv.pk).update(last_message_at=msg.created_at)
    conv.last_message_at = msg.created_at
    return msg


def create_match_conversation(a, b):
    u1, u2 = pair_ids(a, b)
    conv = Conversation.objects.create(user1_id=u1, user2_id=u2, tier=Conversation.MATCH,
                                       origin=Conversation.ORIGIN_MATCH)
    system_message(conv, TEXT_MATCHED.format(n=settings.MATCH_CONNECT_MESSAGES))
    return conv


def connected_conversation(a, b):
    return Conversation.objects.filter(pair_q(a, b), tier__in=[Conversation.INBOX, Conversation.PRIORITY]).first()


def message_counts(conv, stage=None, cap=None):
    """{user_id: messages they sent}, system messages excluded. With `cap`, counting
    stops there (enough for "34/100" bars), so the cost doesn't grow with chat length."""
    counts = {}
    for uid in (conv.user1_id, conv.user2_id):
        qs = conv.messages.filter(sender_id=uid)
        if stage:
            qs = qs.filter(stage=stage)
        counts[uid] = qs[:cap].count() if cap else qs.count()
    return counts


def unread_by_conversation(user):
    """{conversation_id: (tier, unread_count)} for everything with unread messages."""
    rows = (
        Message.objects.filter(
            Q(conversation__user1=user, id__gt=F("conversation__user1_last_read_id"))
            | Q(conversation__user2=user, id__gt=F("conversation__user2_last_read_id"))
        )
        .exclude(sender=user)
        .values("conversation_id", "conversation__tier")
        .annotate(n=Count("id"))
    )
    return {r["conversation_id"]: (r["conversation__tier"], r["n"]) for r in rows}


# ---- connections -----------------------------------------------------------

def connect_users(a, b, via_conversation=None):
    """Create the connection and put the pair's chat in the Inbox.

    If they were chatting in matchmaking, that same conversation is promoted
    (its matchmaking messages become archived history); otherwise a new inbox
    conversation is started. Either way the welcome system message is posted.
    """
    u1, u2 = pair_ids(a, b)
    Connection.objects.get_or_create(user1_id=u1, user2_id=u2)
    now = timezone.now()
    ConnectionRequest.objects.filter(
        Q(from_user_id=u1, to_user_id=u2) | Q(from_user_id=u2, to_user_id=u1), status=ConnectionRequest.PENDING,
    ).update(status=ConnectionRequest.ACCEPTED, responded_at=now)
    forget_connections(a)
    forget_connections(b)

    existing = connected_conversation(a, b)
    if existing:
        return existing
    conv = via_conversation
    if conv is None:
        conv = (Conversation.objects.filter(pair_q(a, b), tier=Conversation.MATCH, is_active=True)
                .order_by("-created_at").first())
    if conv is not None:
        conv.tier = Conversation.INBOX
        conv.tier_changed_at = now
        conv.is_active = True
        conv.save(update_fields=["tier", "tier_changed_at", "is_active"])
    else:
        conv = Conversation.objects.create(user1_id=u1, user2_id=u2, tier=Conversation.INBOX,
                                           origin=Conversation.ORIGIN_CONNECTION, tier_changed_at=now)
    system_message(conv, TEXT_INBOX)
    return conv


def accept_connection_request(req, by_user):
    with transaction.atomic():
        req = ConnectionRequest.objects.select_for_update().select_related("conversation").get(pk=req.pk)
        if req.to_user_id != by_user.pk or req.status != ConnectionRequest.PENDING:
            raise WorkflowError("This request is no longer pending.")
        blocked = (visible_block if req.source == ConnectionRequest.SOURCE_PROFILE else any_block)
        if blocked(req.from_user_id, req.to_user_id):
            raise WorkflowError("You can't connect while one of you has blocked the other.")
        if not same_age_group(req.from_user, req.to_user):
            raise WorkflowError(AGE_GROUP_MESSAGE)
        req.status = ConnectionRequest.ACCEPTED
        req.responded_at = timezone.now()
        req.save(update_fields=["status", "responded_at"])
        via = req.conversation if req.conversation and req.conversation.tier == Conversation.MATCH else None
        return connect_users(req.from_user, req.to_user, via_conversation=via)


def match_connect_state(conv, user):
    """Everything the matchmaking chat panel needs about the Connect button."""
    need = settings.MATCH_CONNECT_MESSAGES
    counts = message_counts(conv, stage=Conversation.MATCH, cap=need)
    other_id = conv.other_id(user)
    pending = conv.connection_requests.filter(status=ConnectionRequest.PENDING).first()
    unlocked = counts[user.pk] >= need and counts[other_id] >= need
    return {
        "need": need,
        "mine": counts[user.pk],
        "theirs": counts[other_id],
        "unlocked": unlocked,
        "outgoing": pending if pending and pending.from_user_id == user.pk else None,
        "incoming": pending if pending and pending.to_user_id == user.pk else None,
        "can_send": unlocked and pending is None and conv.is_active,
    }


def send_match_connection_request(conv, user):
    with transaction.atomic():
        conv = Conversation.objects.select_for_update().get(pk=conv.pk)
        if conv.tier != Conversation.MATCH or not conv.is_active:
            raise WorkflowError("This chat can't send connection requests any more.")
        if messages_blocked(conv.user1_id, conv.user2_id):
            raise WorkflowError("Messages are blocked in this chat.")
        state = match_connect_state(conv, user)
        if state["incoming"]:
            return accept_connection_request(state["incoming"], user)
        if not state["can_send"]:
            raise WorkflowError(f"You both need {state['need']} messages first.")
        ConnectionRequest.objects.create(from_user=user, to_user_id=conv.other_id(user),
                                         source=ConnectionRequest.SOURCE_MATCH, conversation=conv)
    return None


# ---- priority ----------------------------------------------------------------

def priority_state(conv, user):
    need = settings.PRIORITY_MESSAGES
    counts = message_counts(conv, cap=need)
    other_id = conv.other_id(user)
    pending = conv.priority_requests.filter(status=PriorityRequest.PENDING).first()
    unlocked = counts[user.pk] >= need and counts[other_id] >= need
    return {
        "need": need,
        "mine": counts[user.pk],
        "theirs": counts[other_id],
        "unlocked": unlocked,
        "outgoing": pending if pending and pending.from_user_id == user.pk else None,
        "incoming": pending if pending and pending.to_user_id == user.pk else None,
        "can_send": unlocked and pending is None and conv.tier == Conversation.INBOX,
    }


def send_priority_request(conv, user):
    with transaction.atomic():
        conv = Conversation.objects.select_for_update().get(pk=conv.pk)
        if conv.tier != Conversation.INBOX:
            raise WorkflowError("Only inbox chats can become priority.")
        if not conv.is_active:
            raise WorkflowError("This chat has ended.")
        if messages_blocked(conv.user1_id, conv.user2_id):
            raise WorkflowError("Messages are blocked in this chat.")
        state = priority_state(conv, user)
        if state["incoming"]:
            return accept_priority_request(state["incoming"], user)
        if not state["can_send"]:
            raise WorkflowError(f"You both need {state['need']} messages first.")
        PriorityRequest.objects.create(conversation=conv, from_user=user, to_user_id=conv.other_id(user))
    return None


def accept_priority_request(preq, by_user):
    with transaction.atomic():
        preq = PriorityRequest.objects.select_for_update().get(pk=preq.pk)
        if preq.to_user_id != by_user.pk or preq.status != PriorityRequest.PENDING:
            raise WorkflowError("This request is no longer pending.")
        conv = Conversation.objects.select_for_update().get(pk=preq.conversation_id)
        if not conv.is_active:
            raise WorkflowError("This chat has ended.")
        now = timezone.now()
        preq.status = PriorityRequest.ACCEPTED
        preq.responded_at = now
        preq.save(update_fields=["status", "responded_at"])
        if conv.tier == Conversation.INBOX:
            conv.tier = Conversation.PRIORITY
            conv.tier_changed_at = now
            conv.save(update_fields=["tier", "tier_changed_at"])
            system_message(conv, TEXT_PRIORITY)
        return conv
