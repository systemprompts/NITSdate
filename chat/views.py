import hashlib
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
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

from moderation.permissions import restriction_for

from . import services
from .models import Conversation, Message, PriorityRequest

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
                       last_sender=Subquery(last.values("sender_id")[:1]))
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
    tabs = [{"key": t, "label": TAB_LABELS[t], "unread": tab_unread[t], "count": tab_count.get(t, 0)} for t in TAB_ORDER]
    return render(request, "chat/inbox.html", {"tab": tab, "tabs": tabs, "conversations": page, "page": page,
                                               "inbox_version": services.inbox_version(me, sum(tab_unread.values()))})


@login_required
def badges(request):
    """Polled by every open page so the nav badges and the Messages list update on their own."""
    from accounts.context_processors import badge_counts
    data = badge_counts(request.user)
    data["inbox"] = services.inbox_version(request.user, data["messages"])
    return JsonResponse(data)


def _panel(request, conv):
    """Action panel (Connect / Set priority / Accept ...). Rendered with the
    request so its forms get CSRF tokens."""
    me = request.user
    if messages_blocked(conv.user1_id, conv.user2_id):
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


def _render_messages(msgs, me, conv):
    return render_to_string("chat/_messages.html", {"messages_list": msgs, "me": me, "conv": conv})


@login_required
def room(request, pk):
    me = request.user
    conv = _get_conversation(request, pk)
    other = conv.other(me)
    # only the newest PAGE messages; older ones and archives load on demand (see history())
    current = list(conv.messages.filter(stage=conv.tier).order_by("-id")[:PAGE])[::-1]
    has_earlier = bool(current) and conv.messages.filter(stage=conv.tier, id__lt=current[0].pk).exists()
    stage_counts = dict(conv.messages.filter(stage__in=conv.archived_stages())
                        .values_list("stage").annotate(n=Count("id", filter=Q(sender__isnull=False))))
    present = set(conv.messages.filter(stage__in=conv.archived_stages()).values_list("stage", flat=True).distinct())
    archived = [{"stage": s, "label": STAGE_ARCHIVE_LABELS[s], "count": stage_counts.get(s, 0)}
                for s in conv.archived_stages() if s in present]
    panel_html, panel_key = _panel(request, conv)
    last_id = conv.messages.order_by("-id").values_list("id", flat=True).first() or 0
    if last_id:
        conv.mark_read(me, last_id)
    return render(request, "chat/room.html", {
        "conv": conv,
        "other": other,
        "title": _title(conv, me),
        "name_hidden": not conv.is_match and name_is_hidden(other, me),
        "gender": None if conv.is_match else visible_gender(other, me),
        "blind_name": conv.nickname_for(other) if conv.is_match else None,
        "my_blind_name": conv.nickname_for(me) if conv.is_match else None,
        "archived": archived,
        "messages_html": _render_messages(current, me, conv),
        "last_id": last_id,
        "first_id": current[0].pk if current else 0,
        "has_earlier": has_earlier,
        "panel_html": panel_html,
        "panel_key": panel_key,
        "msg_blocked": messages_blocked(me, other),
        "no_messaging": restriction_for(me).no_messaging,
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
    qs = conv.messages.filter(stage=stage).order_by("-id")
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
    new = list(conv.messages.filter(id__gt=after, stage=conv.tier))
    panel_html, panel_key = _panel(request, conv)
    if new:
        conv.mark_read(me, new[-1].pk)
    return JsonResponse({
        "tier": conv.tier,
        "active": conv.is_active,
        "blocked": messages_blocked(conv.user1_id, conv.user2_id),
        "last_id": new[-1].pk if new else after,
        "html": _render_messages(new, me, conv) if new else "",
        "panel": panel_html,
        "panel_key": panel_key,
    })


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
            image = process_image(upload)
        except ValidationError as exc:
            return fail(exc.messages[0])
    msg = Message(conversation=conv, sender=me, stage=conv.tier, text=text)
    if image:
        msg.image.save(image.name, image, save=False)
    msg.save()
    Conversation.objects.filter(pk=conv.pk).update(last_message_at=msg.created_at)
    conv.mark_read(me, msg.pk)
    if _is_fetch(request):
        return JsonResponse({"ok": True})
    return redirect("chat_room", pk=pk)


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
    if not msg.conversation.has_member(request.user):
        raise Http404
    return serve_image(msg.image)
