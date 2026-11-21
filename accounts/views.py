import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from chat.services import WorkflowError, accept_connection_request, connected_conversation
from feed.models import Post
from feed.visibility import visible_q
from moderation.models import FormerAccount, ModeratorGrant
from moderation.permissions import is_mod, request_limit
from moderation.services import account_summary, delete_account

from .campuses import open_campuses
from .forms import OnboardingForm, clean_field_input
from chat.models import Conversation, PriorityRequest

from .models import Block, Connection, ConnectionRequest, GalleryPhoto, Profile, User
from .privacy import (AGE_GROUP_MESSAGE, BLOCKED, CONNECTION, NOT_BLIND, SELF, STRANGER, are_connected, visible_block,
                      display_name, field_visible, forget_connections, gallery_visible, get_profile, photo_visible,
                      profile_rows, relation, same_age_group)
from .images import process_image, serve_image
from .profile_fields import (BRANCH_SUGGESTIONS, CONNECTIONS, FIELD_MAP, GENDER_CHOICES, PRIVACY_CHOICES,
                             PUBLIC)


def _next_or(request, fallback):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return fallback


# ---- sign in -----------------------------------------------------------------

def login_page(request):
    if request.user.is_authenticated:
        return redirect("home")
    return render(request, "accounts/login.html", {
        "google_ready": bool(settings.SOCIALACCOUNT_PROVIDERS["google"]["APP"]["client_id"]),
        "campuses": open_campuses(),
        "any_account": settings.OPEN_CAMPUSES == "any",
    })


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def _unique_username(email):
    base = re.sub(r"[^\w.@+-]", "", email.split("@")[0])[:120] or "user"
    username, n = base, 1
    while User.objects.filter(username=username).exists():
        n += 1
        username = f"{base}{n}"
    return username


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def _is_local(request):
    """True only when the site is opened on this computer (not through ngrok etc.).
    The tunnel connects from 127.0.0.1 too, so the Host header is what tells them apart."""
    return request.get_host().rsplit(":", 1)[0].lower() in LOCAL_HOSTS


def _privileged_email(email):
    email = (email or "").lower()
    return (email in settings.ADMIN_EMAILS or ModeratorGrant.objects.filter(email__iexact=email).exists()
            or User.objects.filter(email__iexact=email, is_superuser=True).exists())


def dev_login(request):
    """Password-less login for testing with many accounts (DEV_LOGIN=1).
    Through a public link (ngrok), admin and moderator accounts are off-limits."""
    if not settings.DEV_LOGIN:
        raise Http404
    local = _is_local(request)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "login":
            user = get_object_or_404(User, pk=request.POST.get("user_id"))
            if not local and is_mod(user):
                messages.error(request, "Admin and moderator accounts can only use test login on this computer.")
                return redirect("dev_login")
        elif action == "create":
            name = request.POST.get("name", "").strip()[:60]
            email = request.POST.get("email", "").strip().lower()
            if not name or "@" not in email:
                messages.error(request, "Enter a name and an email address.")
                return redirect("dev_login")
            if not local and _privileged_email(email):
                messages.error(request, "That email belongs to an admin or moderator. Use a different one.")
                return redirect("dev_login")
            user = User.objects.filter(email=email).first()
            if user is None:
                first, _, last = name.partition(" ")
                user = User(username=_unique_username(email), email=email, first_name=first[:150],
                            last_name=last[:150])
                user.set_unusable_password()
                user.save()
            request.session["onboarding_name"] = name
        else:
            return HttpResponseBadRequest()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("home")
    users = list(User.objects.select_related("profile").order_by("-date_joined")[:300])
    if not local:
        users = [u for u in users if not is_mod(u)]
    return render(request, "accounts/dev_login.html", {"users": users, "remote": not local})


@login_required
def onboarding(request):
    if request.user.has_profile:
        return redirect("home")
    initial_name = request.session.get("onboarding_name") or request.user.get_full_name()
    # coming back after deleting an account: the date of birth stays what it was
    former = (FormerAccount.objects.filter(email__iexact=request.user.email).exclude(birth_date=None).first()
              if request.user.email else None)
    initial = {"name": initial_name, **({"birth_date": former.birth_date} if former else {})}
    form = OnboardingForm(request.POST or None, initial=initial)
    if former:
        form.fields["birth_date"].disabled = True   # ignores whatever is posted
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        order = [g for g, _ in GENDER_CHOICES]
        hidden_name = CONNECTIONS if d["randomize_name"] else PUBLIC
        profile = Profile(user=request.user, name=d["name"].strip(), gender=d["gender"], birth_date=d["birth_date"],
                          likes=",".join(g for g in order if g in d["likes"]),
                          profile_visible=d["profile_visible"])
        profile.init_meta({"name": hidden_name, "photo": hidden_name})
        profile.save()
        request.session.pop("onboarding_name", None)
        messages.success(request, "Welcome to nitsdate! Add more about yourself whenever you like — "
                                  "every detail has its own privacy button.")
        return redirect("my_profile")
    return render(request, "accounts/onboarding.html", {"form": form, "alias": request.user.alias,
                                                        "dob_kept": bool(former)})


# ---- own profile -----------------------------------------------------------------

def requests_left_today(user):
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    used = ConnectionRequest.objects.filter(from_user=user, source=ConnectionRequest.SOURCE_PROFILE,
                                            created_at__gte=start).count()
    return max(0, request_limit(user) - used)


@login_required
def my_profile(request):
    profile = request.user.profile
    rows = profile_rows(profile, SELF)
    return render(request, "accounts/me.html", {
        "profile": profile,
        "basics": [r for r in rows if r["field"].section == "basics"],
        "about": [r for r in rows if r["field"].section == "about"],
        "privacy_choices": PRIVACY_CHOICES,
        "branch_suggestions": BRANCH_SUGGESTIONS,
        "requests_left": requests_left_today(request.user),
        "per_day": request_limit(request.user),
        "photos": list(GalleryPhoto.objects.filter(user=request.user)),
        "min_age": settings.MIN_AGE,
        "gallery_max": settings.GALLERY_MAX_PHOTOS,
    })


@login_required
@require_POST
def edit_field(request, key):
    field = FIELD_MAP.get(key)
    if field is None:
        raise Http404
    profile = request.user.profile
    back = f"{reverse('my_profile')}#f-{key}"
    try:
        value = clean_field_input(field, request)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect(back)

    was_empty = not profile.photo if field.kind == "image" else getattr(profile, key) in ("", None)
    if field.kind == "image":
        old_name = profile.photo.name if profile.photo else ""
        if value == "":
            changed = bool(old_name)
            profile.photo = ""
        else:
            profile.photo.save(value.name, value, save=False)
            changed = True
        if old_name and changed:
            profile.photo.storage.delete(old_name)
    else:
        changed = getattr(profile, key) != value
        setattr(profile, key, value)

    if changed:
        counted = profile.note_change(key, was_empty)  # privacy changes never reach here, so they don't count
        profile.save()
        messages.success(request, f"{field.label} {'updated' if counted else 'added'}.")
    else:
        messages.info(request, "Nothing changed.")
    return redirect(back)


@login_required
@require_POST
def set_privacy(request, key):
    if key not in FIELD_MAP:
        raise Http404
    privacy = request.POST.get("privacy")
    if privacy not in dict(PRIVACY_CHOICES):
        return HttpResponseBadRequest("bad privacy value")
    profile = request.user.profile
    profile.set_privacy(key, privacy)
    profile.save(update_fields=["field_meta", "updated_at"])
    messages.success(request, f"{FIELD_MAP[key].label}: visible to {dict(PRIVACY_CHOICES)[privacy].lower()}.")
    return redirect(f"{reverse('my_profile')}#f-{key}")


@login_required
@require_POST
def set_visibility(request):
    profile = request.user.profile
    profile.profile_visible = request.POST.get("visible") == "1"
    profile.save(update_fields=["profile_visible", "updated_at"])
    if profile.profile_visible:
        messages.success(request, "Your profile is unlocked. Public fields are visible to everyone.")
    else:
        messages.success(request, "Your profile is locked. Only connections can see your name and details.")
    return redirect("my_profile")


STAY_SINGLE = "I want to stay single"


@login_required
def delete_my_account(request):
    """Delete your own account and everything in it, once you've typed the phrase."""
    me = request.user
    if me.is_superuser:   # so the site can't lose its last admin by accident
        messages.error(request, "Admin accounts can't be deleted from here.")
        return redirect("my_profile")
    if request.method == "POST":
        typed = " ".join(request.POST.get("phrase", "").split()).strip('"“”')   # the quotes are optional
        if typed.casefold() != STAY_SINGLE.casefold():
            messages.error(request, f'Type "{STAY_SINGLE}" to confirm.')
            return redirect("delete_my_account")
        delete_account(me, remove_content=True)
        logout(request)
        messages.success(request, "Your account and everything in it has been deleted. Take care 💛")
        return redirect("login")
    return render(request, "accounts/delete_account.html", {"summary": account_summary(me), "phrase": STAY_SINGLE})


# ---- other people's profiles -----------------------------------------------------------

@login_required
def profile_view(request, user_id):
    owner = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    profile = get_profile(owner)
    if profile is None:
        raise Http404
    me = request.user
    preview = request.GET.get("as") if owner.pk == me.pk else None
    if owner.pk == me.pk and preview not in ("public", "connection"):
        return redirect("my_profile")
    if preview:
        rel = CONNECTION if preview == "connection" else STRANGER
    else:
        rel = relation(owner, me)
    if rel == BLOCKED:
        return render(request, "accounts/unavailable.html", status=404)

    state, pending, conversation = "none", None, None
    if not preview:
        if rel == CONNECTION:
            state = "connected"
            conversation = connected_conversation(me, owner)
        else:
            # only requests made from profiles: a matchmaking request belongs to a blind chat
            pending = ConnectionRequest.objects.filter(
                Q(from_user=me, to_user=owner) | Q(from_user=owner, to_user=me),
                status=ConnectionRequest.PENDING, source=ConnectionRequest.SOURCE_PROFILE).first()
            if pending:
                state = "outgoing" if pending.from_user_id == me.pk else "incoming"

    return render(request, "accounts/profile.html", {
        "owner": owner,
        "profile": profile,
        "rel": rel,
        "preview": preview,
        "name": profile.name if field_visible(profile, "name", rel) else owner.alias,
        "name_hidden": not field_visible(profile, "name", rel),
        "gender": profile.gender if field_visible(profile, "gender", rel) else None,
        "show_photo": bool(profile.photo) and field_visible(profile, "photo", rel),
        "locked": not profile.profile_visible and rel == STRANGER,
        "rows": [r for r in profile_rows(profile, rel) if r["key"] not in ("name", "photo")],
        "state": state,
        "pending": pending,
        "conversation": conversation,
        "requests_left": requests_left_today(me),
        "per_day": request_limit(request.user),
        "posts": Post.objects.filter(visible_q(me), author=owner)[:5],   # not another NIT's campus posts
        "photos": [ph for ph in GalleryPhoto.objects.filter(user=owner) if gallery_visible(ph, profile, rel)],
        "hidden_photos": sum(1 for ph in GalleryPhoto.objects.filter(user=owner) if not gallery_visible(ph, profile, rel)),
        "my_block": None if preview else Block.objects.filter(NOT_BLIND, blocker=me, blocked=owner).first(),
        "same_group": preview or same_age_group(me, owner),
    })


@login_required
def profile_photo(request, user_id):
    owner = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    if not photo_visible(owner, request.user):
        raise Http404
    return serve_image(owner.profile.photo)


@login_required
@require_POST
def send_request(request, user_id):
    me = request.user
    owner = get_object_or_404(User, pk=user_id)
    back = reverse("profile", args=[owner.pk])
    if owner.pk == me.pk or get_profile(owner) is None:
        raise Http404
    with transaction.atomic():
        User.objects.select_for_update().get(pk=me.pk)  # serialise this user's requests for the daily limit
        if are_connected(me, owner):
            messages.info(request, "You're already connected.")
            return redirect(back)
        if visible_block(me, owner):
            messages.error(request, "You can't send a request while one of you has blocked the other.")
            return redirect(back)
        if not same_age_group(me, owner):
            messages.error(request, AGE_GROUP_MESSAGE)
            return redirect(back)
        incoming = ConnectionRequest.objects.filter(from_user=owner, to_user=me, status=ConnectionRequest.PENDING,
                                                    source=ConnectionRequest.SOURCE_PROFILE).first()
        if incoming:
            conv = accept_connection_request(incoming, me)
            messages.success(request, "They had already asked — you're connected now!")
            return redirect("chat_room", pk=conv.pk)
        if ConnectionRequest.objects.filter(from_user=me, to_user=owner, status=ConnectionRequest.PENDING,
                                            source=ConnectionRequest.SOURCE_PROFILE).exists():
            messages.info(request, "Request already sent.")
            return redirect(back)
        if requests_left_today(me) <= 0:
            if request_limit(me) == 0:
                messages.error(request, "A moderator has turned off profile connection requests for your account.")
            else:
                messages.error(request, f"You've used all {request_limit(me)} profile requests for "
                                        "today. Matchmaking requests are unlimited.")
            return redirect(back)
        ConnectionRequest.objects.create(from_user=me, to_user=owner, source=ConnectionRequest.SOURCE_PROFILE)
    messages.success(request, "Connection request sent.")
    return redirect(back)


PEOPLE_PER_PAGE = 40


@login_required
def people(request):
    me = request.user
    tab = request.GET.get("tab", "requests")
    received_qs = (ConnectionRequest.objects.filter(to_user=me, status=ConnectionRequest.PENDING)
                   .select_related("from_user__profile", "conversation").order_by("-created_at"))
    sent_qs = (ConnectionRequest.objects.filter(from_user=me, status=ConnectionRequest.PENDING)
               .select_related("to_user__profile", "conversation").order_by("-created_at"))
    conn_qs = (Connection.objects.filter(Q(user1=me) | Q(user2=me))
               .select_related("user1__profile", "user2__profile").order_by("-created_at"))
    block_qs = Block.objects.filter(blocker=me).select_related("blocked__profile", "conversation").order_by("-updated_at")
    counts = {"received": received_qs.count(), "sent": sent_qs.count(), "connections": conn_qs.count(),
              "blocks": block_qs.count()}
    source = {"requests": received_qs, "sent": sent_qs, "blocked": block_qs}.get(tab, conn_qs)
    page = Paginator(source, PEOPLE_PER_PAGE).get_page(request.GET.get("page"))

    received = sent = connections = blocks = []
    if tab in ("requests", "sent"):
        rows = list(page)
        for r in rows:
            if r.source == ConnectionRequest.SOURCE_MATCH and r.conversation:
                other = r.from_user if r.to_user_id == me.pk else r.to_user
                r.blind = r.conversation.nickname_for(other)
        received, sent = (rows, []) if tab == "requests" else ([], rows)
    elif tab == "blocked":
        for b in page:
            blind = b.conversation is not None and b.conversation.tier == Conversation.MATCH
            blocks.append({"block": b, "user": b.blocked, "blind": blind,
                           "label": b.conversation.nickname_for(b.blocked) if blind else display_name(b.blocked, me)})
    else:
        rows = list(page)
        pair_q = Q(pk__in=[])
        for c in rows:
            pair_q |= Q(user1_id=c.user1_id, user2_id=c.user2_id)
        chats = {(cv.user1_id, cv.user2_id): cv for cv in Conversation.objects.filter(
            pair_q, tier__in=[Conversation.INBOX, Conversation.PRIORITY])}      # one query for the whole page
        connections = [{"user": c.other(me), "since": c.created_at, "conversation": chats.get((c.user1_id, c.user2_id))}
                       for c in rows]
    return render(request, "accounts/people.html", {
        "tab": tab, "received": received, "sent": sent, "connections": connections, "blocks": blocks,
        "counts": counts, "page": page,
        "requests_left": requests_left_today(me), "per_day": request_limit(request.user),
    })


@login_required
@require_POST
def request_action(request, pk, action):
    me = request.user
    req = get_object_or_404(ConnectionRequest, pk=pk)
    fallback = reverse("people")
    if action == "accept":
        try:
            conv = accept_connection_request(req, me)
        except WorkflowError as exc:
            messages.error(request, str(exc))
            return redirect(_next_or(request, fallback))
        messages.success(request, "You're connected! Your chat is now in the Inbox.")
        return redirect("chat_room", pk=conv.pk)
    if req.status != ConnectionRequest.PENDING:
        messages.info(request, "This request is no longer pending.")
    elif action == "decline" and req.to_user_id == me.pk:
        req.status, req.responded_at = ConnectionRequest.DECLINED, timezone.now()
        req.save(update_fields=["status", "responded_at"])
        messages.info(request, "Request declined.")
    elif action == "cancel" and req.from_user_id == me.pk:
        req.status, req.responded_at = ConnectionRequest.CANCELLED, timezone.now()
        req.save(update_fields=["status", "responded_at"])
        messages.info(request, "Request cancelled.")
    else:
        raise Http404
    return redirect(_next_or(request, fallback))


# ---- blocking -----------------------------------------------------------------

@login_required
@require_POST
def block_user(request, user_id):
    """Block from a profile page. kind=messages|profile, on=1|0."""
    other = get_object_or_404(User, pk=user_id)
    return _set_block(request, other, None)


@login_required
@require_POST
def block_in_chat(request, pk):
    """Block from inside a chat. Addressed by conversation, so a blind
    matchmaking chat never exposes the other person's account id."""
    conv = get_object_or_404(Conversation, pk=pk)
    if not conv.has_member(request.user):
        raise Http404
    return _set_block(request, conv.other(request.user), conv)


def _set_block(request, other, conv):
    me = request.user
    kind = request.POST.get("kind")
    if other.pk == me.pk or kind not in ("messages", "profile"):
        return HttpResponseBadRequest()
    on = request.POST.get("on") == "1"
    with transaction.atomic():
        block, _ = Block.objects.select_for_update().get_or_create(blocker=me, blocked=other)
        setattr(block, kind, on)
        if conv is not None and block.conversation_id is None:
            block.conversation = conv
        if block.messages or block.profile:
            block.save()
        else:
            block.delete()
        if on:  # a block cancels anything pending between the two
            now = timezone.now()
            ConnectionRequest.objects.filter(
                Q(from_user=me, to_user=other) | Q(from_user=other, to_user=me), status=ConnectionRequest.PENDING,
            ).update(status=ConnectionRequest.CANCELLED, responded_at=now)
            PriorityRequest.objects.filter(
                Q(from_user=me, to_user=other) | Q(from_user=other, to_user=me), status=PriorityRequest.PENDING,
            ).update(status=PriorityRequest.CANCELLED, responded_at=now)
    text = {
        ("messages", True): "Messages blocked. Neither of you can send messages in your chats.",
        ("messages", False): "Messages unblocked.",
        ("profile", True): "Your profile is now hidden from them.",
        ("profile", False): "They can see your profile again (following your privacy settings).",
    }[(kind, on)]
    messages.success(request, text)
    fallback = reverse("chat_room", args=[conv.pk]) if conv else reverse("profile", args=[other.pk])
    return redirect(_next_or(request, fallback))


# ---- photo gallery ----------------------------------------------------------------

@login_required
@require_POST
def gallery_upload(request):
    me = request.user
    back = reverse("my_profile") + "#photos"
    files = request.FILES.getlist("photos")
    privacy = request.POST.get("privacy", PUBLIC)
    if privacy not in dict(PRIVACY_CHOICES):
        return HttpResponseBadRequest()
    if not files:
        messages.error(request, "Choose at least one photo.")
        return redirect(back)
    room = settings.GALLERY_MAX_PHOTOS - GalleryPhoto.objects.filter(user=me).count()
    if room <= 0:
        messages.error(request, f"Your gallery is full ({settings.GALLERY_MAX_PHOTOS} photos). Delete one to add another.")
        return redirect(back)
    saved, problems = 0, []
    for upload in files[:room]:
        try:
            content = process_image(upload)
        except ValidationError as exc:
            problems.append(f"{upload.name}: {exc.messages[0]}")
            continue
        photo = GalleryPhoto(user=me, privacy=privacy)
        photo.image.save(content.name, content, save=False)
        photo.save()
        saved += 1
    if saved:
        messages.success(request, f"Added {saved} photo{'s' if saved != 1 else ''}.")
    if len(files) > room:
        messages.error(request, f"Only {room} more photo{'s' if room != 1 else ''} fit, so "
                                f"{len(files) - room} {'were' if len(files) - room != 1 else 'was'} not added "
                                f"(limit {settings.GALLERY_MAX_PHOTOS}).")
    for problem in problems:
        messages.error(request, problem)
    return redirect(back)


@login_required
@require_POST
def gallery_privacy(request, pk):
    photo = get_object_or_404(GalleryPhoto, pk=pk, user=request.user)
    privacy = request.POST.get("privacy")
    if privacy not in dict(PRIVACY_CHOICES):
        return HttpResponseBadRequest()
    photo.privacy = privacy
    photo.save(update_fields=["privacy"])
    messages.success(request, f"Photo visible to: {dict(PRIVACY_CHOICES)[privacy].lower()}.")
    return redirect(reverse("my_profile") + "#photos")


@login_required
@require_POST
def gallery_delete(request, pk):
    photo = get_object_or_404(GalleryPhoto, pk=pk, user=request.user)
    photo.image.delete(save=False)
    photo.delete()
    messages.info(request, "Photo deleted.")
    return redirect(reverse("my_profile") + "#photos")


@login_required
@require_POST
def gallery_make_avatar(request, pk):
    photo = get_object_or_404(GalleryPhoto, pk=pk, user=request.user)
    profile = request.user.profile
    with photo.image.open("rb") as fh:
        data = fh.read()
    old_name = profile.photo.name if profile.photo else ""
    profile.photo.save(f"{uuid.uuid4().hex}.jpg", ContentFile(data), save=False)
    if old_name:
        profile.photo.storage.delete(old_name)
    profile.note_change("photo", was_empty=not old_name)
    profile.save()
    messages.success(request, "Profile photo updated.")
    return redirect(reverse("my_profile"))


@login_required
def gallery_image(request, user_id, pk):
    photo = get_object_or_404(GalleryPhoto.objects.select_related("user__profile"), pk=pk, user_id=user_id)
    profile = get_profile(photo.user)
    if profile is None or not gallery_visible(photo, profile, relation(photo.user, request.user)):
        raise Http404
    return serve_image(photo.image)
