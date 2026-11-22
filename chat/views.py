import hashlib
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.images import process_image, serve_image
from accounts.models import Block, ConnectionRequest
from accounts.privacy import display_name, messages_blocked, name_is_hidden, visible_gender

from live.publish import push_conversation
from matchmaking.models import MatchSession
from matchmaking.services import PENDING
from moderation.permissions import restriction_for
from notifications import push

from . import services
from .presence import presence, receipt, shows_presence
from .models import Conversation, Message, MessageEdit, PriorityRequest

PAGE = 50  # messages per load in a chat
TAB_ORDER = [Conversation.PRIORITY, Conversation.INBOX, Conversation.MATCH]
TAB_LABELS = {Conversation.PRIORITY: "Priority", Conversation.INBOX: "Inbox", Conversation.MATCH: "Matchmaking"}
STAGE_ARCHIVE_LABELS = {Conversation.MATCH: "Archived matchmaking chat", Conversation.INBOX: "Archived inbox chat"}


def _is_fetch(request):
    return request.headers.get("x-requested-with") == "fetch"


def _get_conversation(request, pk):
    conv = get_object_or_404(Conversation.objects.select_related("user1__profile", "user2__profile"), pk=pk)
    if not conv.has_member(request.user):
        raise Http404
    return conv


def _title(conv, me):
    other = conv.other(me)
    if conv.is_match:
        return conv.nickname_for(other)
    return display_name(other, me)


@login_required
def inbox(request):
    me = request.user
    unread = services.unread_by_conversation(me)          # one grouped query
    tab_unread = {t: 0 for t in TAB_ORDER}
    for tier, n in unread.values():
        tab_unread[tier] += n
    tab_count = dict(Conversation.objects.filter(services.member_q(me)).values_list("tier").annotate(n=Count("id")))
    tab = request.GET.get("tab")
    if tab not in tab_unread:
        tab = next((t for t in TAB_ORDER if tab_unread[t]), Conversation.INBOX)

    last = Message.objects.filter(conversation=OuterRef("pk")).order_by("-id")
    convs = (Conversation.objects.filter(services.member_q(me), tier=tab)
             .select_related("user1__profile", "user2__profile")
             .annotate(last_text=Subquery(last.values("text")[:1]),
                       last_image=Subquery(last.values("image")[:1]),
                       last_sender=Subquery(last.values("sender_id")[:1]),
                       last_deleted=Subquery(last.values("deleted_at")[:1]),
                       last_removed=Subquery(last.values("removed")[:1]))
             .order_by("-is_active", "-last_message_at", "-id"))   # ended matchmaking chats last
    page = Paginator(convs, 30).get_page(request.GET.get("page"))
    blocked_with = {b if a == me.pk else a for a, b in Block.objects.filter(
        Q(blocker=me) | Q(blocked=me), messages=True).values_list("blocker_id", "blocked_id")}
    for conv in page:
        conv.other_user = conv.other(me)
        conv.title = _title(conv, me)
        conv.name_hidden = not conv.is_match and name_is_hidden(conv.other_user, me)
        conv.gender = None if conv.is_match else visible_gender(conv.other_user, me)  # blind chats stay blind
        conv.unread = unread.get(conv.pk, (None, 0))[1]
        conv.msg_blocked = conv.other_id(me) in blocked_with
        if conv.tier == Conversation.PRIORITY and conv.is_active and not conv.msg_blocked:
            conv.presence = presence(conv.other_user)
    tabs = [{"key": t, "label": TAB_LABELS[t], "unread": tab_unread[t], "count": tab_count.get(t, 0)} for t in TAB_ORDER]
    return render(request, "chat/inbox.html", {"tab": tab, "tabs": tabs, "conversations": page, "page": page,
                                               "inbox_version": services.inbox_version(me, sum(tab_unread.values()))})


@login_required
def badges(request):
    """Asked by open pages that couldn't open a live connection (and once in a while by those that
    did), so the nav badges and the Messages list update on their own."""
    from accounts.context_processors import badge_counts
    data = badge_counts(request.user)
    data["inbox"] = services.inbox_version(request.user, data["messages"])
    return JsonResponse(data)


def _panel(request, conv, blocked):
    """Action panel (Connect / Set priority / Accept ...). Rendered with the
    request so its forms get CSRF tokens."""
    me = request.user
    if blocked:
        return "", "blocked"
    ctx = {"conv": conv, "me": me}
    if conv.tier == Conversation.MATCH:
        ctx["connect"] = services.match_connect_state(conv, me)
    elif conv.tier == Conversation.INBOX:
        ctx["priority"] = services.priority_state(conv, me)
    ctx["csrf_token"] = get_token(request)  # forms need it; skip re-running the nav context processors
    html = render_to_string("chat/_panel.html", ctx)
    # CSRF tokens are re-masked on every render; leave them out of the change-detection key
    key = hashlib.md5(re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "", html).encode()).hexdigest()
    return html, key


def _render_messages(msgs, me, conv, can_write=None):
    """`msgs` should come with select_related("reply_to") (see _messages). `can_write` (reply and
    edit are offered) is worked out if not given."""
    return render_to_string("chat/_messages.html", {
        "messages_list": msgs, "me": me, "conv": conv, "other_name": _title(conv, me),
        "can_write": _can_write(conv, me) if can_write is None else can_write})


def _messages(conv):
    return conv.messages.select_related("reply_to")


def _can_write(conv, me, blocked=None):
    """May `me` send (and so reply and edit) in this chat right now? Pass `blocked` if it's known."""
    if not conv.is_active:
        return False
    if messages_blocked(conv.user1_id, conv.user2_id) if blocked is None else blocked:
        return False
    return not restriction_for(me).no_messaging


CHANGES_OVERLAP = timedelta(seconds=5)   # changes are re-sent this far back, so one committing late isn't missed


def _cursor():
    return round(timezone.now().timestamp(), 3)


@login_required
def room(request, pk):
    me = request.user
    conv = _get_conversation(request, pk)
    other = conv.other(me)
    # only the newest PAGE messages; older ones and archives load on demand (see history())
    current = list(_messages(conv).filter(stage=conv.tier).order_by("-id")[:PAGE])[::-1]
    has_earlier = bool(current) and conv.messages.filter(stage=conv.tier, id__lt=current[0].pk).exists()
    stage_counts = dict(conv.messages.filter(stage__in=conv.archived_stages())
                        .values_list("stage").annotate(n=Count("id", filter=Q(sender__isnull=False))))
    present = set(conv.messages.filter(stage__in=conv.archived_stages()).values_list("stage", flat=True).distinct())
    archived = [{"stage": s, "label": STAGE_ARCHIVE_LABELS[s], "count": stage_counts.get(s, 0)}
                for s in conv.archived_stages() if s in present]
    blocked = messages_blocked(conv.user1_id, conv.user2_id)
    panel_html, panel_key = _panel(request, conv, blocked)
    last_id = conv.messages.order_by("-id").values_list("id", flat=True).first() or 0
    live_presence = shows_presence(conv, blocked)
    # In a Priority chat reading is shown as "Seen", so the page marks it read itself once it's
    # visible (its first poll); a chat opened in a background tab mustn't say "Seen" yet.
    if last_id and not live_presence:
        conv.mark_read(me, last_id)
    # arrived straight from the "matching" screen (the live update sends the page here): the
    # search is settled, so /match/ mustn't bounce back into this chat later
    pending = request.session.get(PENDING)
    if pending and MatchSession.objects.filter(pk=pending, user=me, conversation=conv).exists():
        request.session.pop(PENDING, None)
        messages.success(request, "It's a match! Say hi 👋")
    return render(request, "chat/room.html", {
        "conv": conv,
        "other": other,
        "title": _title(conv, me),
        "name_hidden": not conv.is_match and name_is_hidden(other, me),
        "gender": None if conv.is_match else visible_gender(other, me),
        "blind_name": conv.nickname_for(other) if conv.is_match else None,
        "my_blind_name": conv.nickname_for(me) if conv.is_match else None,
        # a match across NITs says where they are; same-NIT matches don't need it
        "other_campus": (other.campus_name if conv.is_match and other and other.campus != me.campus else ""),
        "archived": archived,
        "messages_html": _render_messages(current, me, conv, _can_write(conv, me, blocked)),
        "last_id": last_id,
        "first_id": current[0].pk if current else 0,
        "has_earlier": has_earlier,
        "changes_cursor": _cursor(),
        "panel_html": panel_html,
        "panel_key": panel_key,
        "msg_blocked": blocked,
        "no_messaging": restriction_for(me).no_messaging,
        # Priority chats: "Active now / Active 5 minutes ago", "typing…" and "Seen"
        "presence": presence(other) if live_presence else None,
        "receipt": receipt(conv, me) if live_presence else "",
        "my_block": Block.objects.filter(blocker=me, blocked=other).first(),
        "tier_label": TAB_LABELS[conv.tier],
    })


@login_required
def history(request, pk):
    """Older messages, PAGE at a time: ?stage=<tier>&before=<message id>.
    Used for "load earlier messages" and for opening archived chats."""
    me = request.user
    conv = _get_conversation(request, pk)
    stage = request.GET.get("stage", conv.tier)
    if stage not in dict(Conversation.TIERS):
        return HttpResponseBadRequest()
    qs = _messages(conv).filter(stage=stage).order_by("-id")
    try:
        if request.GET.get("before"):
            qs = qs.filter(id__lt=int(request.GET["before"]))
    except ValueError:
        return HttpResponseBadRequest()
    batch = list(qs[:PAGE + 1])
    has_more = len(batch) > PAGE
    batch = batch[:PAGE][::-1]
    return JsonResponse({"html": _render_messages(batch, me, conv), "first_id": batch[0].pk if batch else None,
                         "has_more": has_more})


@login_required
def poll(request, pk):
    me = request.user
    conv = _get_conversation(request, pk)
    try:
        after = int(request.GET.get("after", 0))
    except ValueError:
        after = 0
    new = list(_messages(conv).filter(id__gt=after, stage=conv.tier))
    blocked = messages_blocked(conv.user1_id, conv.user2_id)
    can_write = _can_write(conv, me, blocked)
    changed_html, cursor = _changes(request, conv, me, after, can_write)
    panel_html, panel_key = _panel(request, conv, blocked)
    # pages only poll while they're visible, so everything up to what's shown has been seen
    upto = new[-1].pk if new else after
    if upto > conv.last_read_id(me):
        if not new:   # `after` comes from the page: never past the newest real message
            upto = min(upto, conv.messages.order_by("-id").values_list("id", flat=True).first() or 0)
        conv.mark_read(me, upto)
    extra = {}
    if shows_presence(conv, blocked):
        extra = {"presence": presence(conv.other(me)), "receipt": receipt(conv, me)}
    return JsonResponse({
        **extra,
        "tier": conv.tier,
        "active": conv.is_active,
        "blocked": blocked,
        "last_id": new[-1].pk if new else after,
        "html": _render_messages(new, me, conv, can_write) if new else "",
        "changed": changed_html,
        "cursor": cursor,
        "panel": panel_html,
        "panel_key": panel_key,
    })


def _changes(request, conv, me, after, can_write):
    """Messages already on the page (id <= after) that were edited, deleted or removed since
    ?changed=<cursor>, plus replies quoting them (their quote changes too). Returns (html, new cursor)."""
    cursor = _cursor()
    try:
        since = float(request.GET["changed"])
    except (KeyError, ValueError):
        return "", cursor
    since = datetime.fromtimestamp(since, tz=dt_timezone.utc) - CHANGES_OVERLAP
    ids = list(conv.messages.filter(changed_at__gt=since, id__lte=after).values_list("id", flat=True)[:200])
    if not ids:
        return "", cursor
    msgs = list(_messages(conv).filter(Q(id__in=ids) | Q(reply_to_id__in=ids, id__lte=after)).order_by("id")[:400])
    return _render_messages(msgs, me, conv, can_write), cursor


@login_required
@require_POST
def send(request, pk):
    me = request.user
    conv = _get_conversation(request, pk)

    def fail(msg):
        if _is_fetch(request):
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("chat_room", pk=pk)

    if not conv.is_active:
        return fail("This chat has ended.")
    if messages_blocked(conv.user1_id, conv.user2_id):
        return fail("Messages are blocked in this chat.")
    if restriction_for(me).no_messaging:
        return fail("A moderator has turned off messaging for your account.")
    text = request.POST.get("text", "").strip()
    upload = request.FILES.get("image")
    if not text and not upload:
        return fail("Type a message or attach a photo.")
    if len(text) > 2000:
        return fail("Messages can be at most 2,000 characters.")
    image = None
    if upload:
        try:
            image = process_image(upload, allow_gif=True)   # animated GIFs stay animated
        except ValidationError as exc:
            return fail(exc.messages[0])
    reply_to = None
    if request.POST.get("reply_to", "").isdigit():
        # any real message in this chat; a deleted one can still be replied to (the quote says so)
        reply_to = conv.messages.filter(pk=int(request.POST["reply_to"]), sender__isnull=False).first()
    msg = Message(conversation=conv, sender=me, stage=conv.tier, text=text, reply_to=reply_to)
    if image:
        msg.image.save(image.name, image, save=False)
    msg.save()
    push.message_sent(msg)   # their phone, if they turned notifications on (after this commits)
    Conversation.objects.filter(pk=conv.pk).update(last_message_at=msg.created_at)
    conv.mark_read(me, msg.pk, tell_other=False)   # the new message itself already reaches them
    if _is_fetch(request):
        return JsonResponse({"ok": True})
    return redirect("chat_room", pk=pk)


def _own_message(request, msg_id):
    msg = get_object_or_404(Message.objects.select_related("conversation"), pk=msg_id)
    if msg.sender_id != request.user.pk:    # system messages have no sender
        raise Http404
    return msg


def _done(request, conv, error=None):
    if _is_fetch(request):
        return JsonResponse({"ok": not error, **({"error": error} if error else {})}, status=400 if error else 200)
    if error:
        messages.error(request, error)
    return redirect("chat_room", pk=conv.pk)


@login_required
@require_POST
def edit_message(request, msg_id):
    """Change the text of your message. The earlier text is kept for moderators (MessageEdit);
    in the chat it only says "Edited"."""
    msg = _own_message(request, msg_id)
    conv = msg.conversation
    if msg.hidden or not msg.text:
        return _done(request, conv, "This message can't be edited.")
    if not _can_write(conv, request.user):
        return _done(request, conv, "You can't send messages in this chat right now.")
    text = request.POST.get("text", "").strip()
    if not text:
        return _done(request, conv, "A message can't be empty. Delete it instead.")
    if len(text) > 2000:
        return _done(request, conv, "Messages can be at most 2,000 characters.")
    if text == msg.text:
        return _done(request, conv)
    with transaction.atomic():
        locked = Message.objects.select_for_update().get(pk=msg.pk)
        if locked.hidden:
            return _done(request, conv, "This message can't be edited.")
        MessageEdit.objects.create(message=locked, text=locked.text)
        now = timezone.now()
        Message.objects.filter(pk=msg.pk).update(text=text, edited_at=now, changed_at=now)
    push_conversation(conv)
    return _done(request, conv)


@login_required
@require_POST
def delete_message(request, msg_id):
    """Delete your message for both of you: it shows as "Message deleted". Its text and photo stay
    in the database for moderators. Works even after a chat ended or was blocked."""
    msg = _own_message(request, msg_id)
    conv = msg.conversation
    if not msg.hidden:
        now = timezone.now()
        if Message.objects.filter(pk=msg.pk, deleted_at__isnull=True).update(deleted_at=now, changed_at=now):
            push_conversation(conv)
    return _done(request, conv)


@login_required
@require_POST
def request_connection(request, pk):
    conv = _get_conversation(request, pk)
    try:
        result = services.send_match_connection_request(conv, request.user)
    except services.WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        if result is not None:
            messages.success(request, "You're connected! This chat moved to your Inbox.")
        else:
            messages.success(request, "Connection request sent. They'll see it right here in the chat.")
    return redirect("chat_room", pk=pk)


@login_required
@require_POST
def request_priority(request, pk):
    conv = _get_conversation(request, pk)
    try:
        result = services.send_priority_request(conv, request.user)
    except services.WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "You're each other's Priority now!" if result else "Priority request sent.")
    return redirect("chat_room", pk=pk)


@login_required
@require_POST
def priority_action(request, pk, req_id, action):
    me = request.user
    conv = _get_conversation(request, pk)
    preq = get_object_or_404(PriorityRequest, pk=req_id, conversation=conv)
    if action == "accept":
        try:
            services.accept_priority_request(preq, me)
            messages.success(request, "This chat moved to Priority ⭐")
        except services.WorkflowError as exc:
            messages.error(request, str(exc))
    elif preq.status == PriorityRequest.PENDING and (
            (action == "decline" and preq.to_user_id == me.pk) or (action == "cancel" and preq.from_user_id == me.pk)):
        preq.status = PriorityRequest.DECLINED if action == "decline" else PriorityRequest.CANCELLED
        preq.responded_at = timezone.now()
        preq.save(update_fields=["status", "responded_at"])
    else:
        return HttpResponseBadRequest()
    return redirect("chat_room", pk=pk)


@login_required
@require_POST
def leave(request, pk):
    conv = _get_conversation(request, pk)
    if conv.tier != Conversation.MATCH or not conv.is_active:
        return HttpResponseBadRequest()
    conv.is_active = False
    conv.ended_by = request.user
    conv.save(update_fields=["is_active", "ended_by"])
    conv.connection_requests.filter(status=ConnectionRequest.PENDING).update(
        status=ConnectionRequest.CANCELLED, responded_at=timezone.now())
    services.system_message(conv, f"{conv.nickname_for(request.user)} left the chat. This match has ended.")
    messages.info(request, "You left the chat.")
    return redirect(f"/messages/?tab={Conversation.MATCH}")


@login_required
def photo(request, msg_id):
    msg = get_object_or_404(Message.objects.select_related("conversation"), pk=msg_id)
    if not msg.conversation.has_member(request.user) or msg.hidden:
        raise Http404
    return serve_image(msg.image)
