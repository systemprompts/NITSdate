from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
import datetime

from django.db.models import BooleanField, Case, Count, Exists, F, OuterRef, Q, Value, When
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.images import serve_image
from accounts.models import Block, Connection, GalleryPhoto, User
from accounts.privacy import BLOCKED, SELF, display_name, field_visible, get_profile, profile_rows, relation
from accounts.profile_fields import PRIVACY_LABELS
from chat.models import Conversation, Message
from feed.models import Comment, Post
from matchmaking.models import MatchSession

from accounts.adapters import email_allowed, may_sign_in

from .models import AllowedEmail, ModAction, ModeratorGrant, Report, Restriction
from .permissions import is_admin, is_mod
from .services import account_summary, delete_account, describe


def mod_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_mod(request.user):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def admin_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_admin(request.user):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def _next_or(request, fallback):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return fallback


def log(mod, action, details="", target_user=None, report=None):
    ModAction.objects.create(mod=mod, action=action, details=details, target_user=target_user, report=report)


# ---------------------------------------------------------------------------
# Reporting (any signed-in user)
# ---------------------------------------------------------------------------

def _report_page(request, *, kind, reported_user, label, snippet, fallback, snapshot=None, **links):
    """`snippet` is what the reporter is shown (only things they may already see);
    `snapshot` is the fuller copy kept for moderators (defaults to the snippet)."""
    me = request.user
    if reported_user is None or reported_user.pk == me.pk:
        raise Http404
    already = Report.objects.filter(reporter=me, kind=kind, status=Report.OPEN, reported_user=reported_user,
                                    **links).exists()
    error = None
    if request.method == "POST":
        reason = request.POST.get("reason")
        if reason not in dict(Report.REASONS):
            error = "Choose what's wrong."
        else:
            if not already:
                Report.objects.create(kind=kind, reason=reason, details=request.POST.get("details", "").strip()[:2000],
                                      reporter=me, reported_user=reported_user,
                                      snapshot=snippet if snapshot is None else snapshot, **links)
            messages.success(request, "Thanks for reporting. A moderator will review it. "
                                      "The person won't know who reported them.")
            return redirect(_next_or(request, fallback))
    return render(request, "moderation/report_form.html", {
        "label": label, "snippet": snippet, "reasons": Report.REASONS, "already": already, "error": error,
        "next": _next_or(request, fallback),  # validated: never a javascript: or off-site URL
    })


@login_required
def report_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    return _report_page(request, kind=Report.POST, reported_user=post.author, post=post,
                        label=f"Post by {display_name(post.author, request.user)}",
                        snippet=post.title + (f"\n\n{post.body}" if post.body else ""),
                        fallback=reverse("post_detail", args=[pk]))


@login_required
def report_comment(request, pk):
    comment = get_object_or_404(Comment, pk=pk, is_deleted=False)
    kind_label = "Reply" if comment.parent_id else "Comment"
    return _report_page(request, kind=Report.COMMENT, reported_user=comment.author, comment=comment,
                        post=comment.post,
                        label=f"{kind_label} by {display_name(comment.author, request.user)}", snippet=comment.body,
                        fallback=reverse("post_detail", args=[comment.post_id]) + f"#c{pk}")


def _chat_name(conv, other, me):
    return conv.nickname_for(other) if conv.is_match else display_name(other, me)


@login_required
def report_message(request, pk):
    msg = get_object_or_404(Message.objects.select_related("conversation"), pk=pk, sender__isnull=False)
    conv = msg.conversation
    if not conv.has_member(request.user):
        raise Http404
    sender = msg.sender
    snippet = msg.text or ""
    if msg.image:
        snippet = (snippet + "\n" if snippet else "") + "[photo]"
    return _report_page(request, kind=Report.MESSAGE, reported_user=sender, message=msg, conversation=conv,
                        label=f"Message from {_chat_name(conv, sender, request.user)}", snippet=snippet,
                        fallback=reverse("chat_room", args=[conv.pk]))


@login_required
def report_profile(request, user_id):
    owner = get_object_or_404(User, pk=user_id)
    profile = get_profile(owner)
    rel = relation(owner, request.user)
    if profile is None or rel == BLOCKED:
        raise Http404
    shown = display_name(owner, request.user)
    if profile.bio and field_visible(profile, "bio", rel):
        shown += f"\n\n{profile.bio}"
    full = f"{profile.name} ({owner.alias})" + (f"\n\n{profile.bio}" if profile.bio else "")
    return _report_page(request, kind=Report.PROFILE, reported_user=owner,
                        label=f"Profile of {display_name(owner, request.user)}", snippet=shown, snapshot=full,
                        fallback=reverse("profile", args=[owner.pk]))


@login_required
def report_chat(request, pk):
    """Report the other person in a chat. Addressed by conversation so blind chats stay blind."""
    conv = get_object_or_404(Conversation, pk=pk)
    if not conv.has_member(request.user):
        raise Http404
    other = conv.other(request.user)
    return _report_page(request, kind=Report.PROFILE, reported_user=other, conversation=conv,
                        label=f"{_chat_name(conv, other, request.user)}, from your {conv.get_tier_display().lower()} chat",
                        snippet=f"Reported from a {conv.get_tier_display().lower()} chat",
                        fallback=reverse("chat_room", args=[conv.pk]))


# ---------------------------------------------------------------------------
# Moderator area
# ---------------------------------------------------------------------------

def minor_cutoff():
    """Anyone born after this date is under 18 today."""
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - settings.ADULT_AGE)
    except ValueError:
        return today.replace(year=today.year - settings.ADULT_AGE, day=28)


@mod_required
def reports(request):
    tab = request.GET.get("tab", "open")
    cutoff = minor_cutoff()
    qs = Report.objects.select_related("reporter__profile", "reported_user__profile").annotate(
        involves_minor=Case(
            When(Q(reported_user__profile__birth_date__gt=cutoff) | Q(reporter__profile__birth_date__gt=cutoff),
                 then=Value(True)), default=Value(False), output_field=BooleanField()),
    ).order_by("-involves_minor", "-created_at")  # reports involving under-18s first
    qs = qs.filter(status=Report.OPEN) if tab == "open" else qs.exclude(status=Report.OPEN)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    open_counts = dict(Report.objects.filter(status=Report.OPEN, reported_user__isnull=False)
                       .values_list("reported_user").annotate(n=Count("id")))
    for r in page:
        r.user_open_reports = open_counts.get(r.reported_user_id, 0)
    return render(request, "moderation/reports.html", {"page": page, "tab": tab,
                                                        "open_count": Report.objects.filter(status=Report.OPEN).count()})


def _content_state(report):
    if report.kind == Report.POST:
        return "exists" if report.post_id else "deleted"
    if report.kind == Report.COMMENT:
        if not report.comment_id:
            return "deleted"
        return "removed" if report.comment.removed else ("deleted" if report.comment.is_deleted else "exists")
    if report.kind == Report.MESSAGE:
        if not report.message_id:
            return "deleted"
        return "removed" if report.message.removed else "exists"
    return "exists"


@mod_required
def report_detail(request, pk):
    report = get_object_or_404(Report.objects.select_related(
        "reporter__profile", "reported_user__profile", "post", "comment", "message", "conversation", "handled_by"), pk=pk)
    me = request.user

    if request.method == "POST":
        action = request.POST.get("action")
        note = request.POST.get("note", "").strip()[:2000]
        if report.status != Report.OPEN and action != "reopen":
            messages.info(request, "This report was already handled.")
            return redirect("mod_report", pk=pk)
        if action == "remove":
            if report.kind == Report.PROFILE or _content_state(report) != "exists":
                messages.error(request, "There's no content left to remove.")
                return redirect("mod_report", pk=pk)
            # close this and every other open report about the same content while it still exists,
            # then remove it (deleting a post would otherwise orphan the sibling reports)
            _close(report, me, Report.RESOLVED, note or f"Removed the {report.kind}.")
            _remove_content(report)
            log(me, f"removed {report.kind}", note, report.reported_user, report)
            messages.success(request, f"{report.get_kind_display()} removed and report closed.")
        elif action == "resolve":
            log(me, "resolved report", note, report.reported_user, report)
            _close(report, me, Report.RESOLVED, note)
            messages.success(request, "Report marked as handled.")
        elif action == "dismiss":
            log(me, "dismissed report", note, report.reported_user, report)
            _close(report, me, Report.DISMISSED, note)
            messages.success(request, "Report dismissed.")
        elif action == "reopen":
            report.status, report.handled_by, report.handled_at = Report.OPEN, None, None
            report.save(update_fields=["status", "handled_by", "handled_at"])
            log(me, "reopened report", note, report.reported_user, report)
        else:
            return HttpResponseBadRequest()
        return redirect("mod_report", pk=pk)

    sizes = settings.MOD_CONTEXT_SIZES
    try:
        n = int(request.GET.get("n", sizes[0]))
    except ValueError:
        n = sizes[0]
    if n not in sizes:
        n = sizes[0]
    context_msgs = []
    if report.conversation_id:
        context_msgs = list(_window(report).select_related("sender__profile")[:n])[::-1]
    return render(request, "moderation/report_detail.html", {
        "report": report, "state": _content_state(report), "sizes": sizes, "n": n, "context_msgs": context_msgs,
        "other_reports": Report.objects.filter(reported_user=report.reported_user).exclude(pk=pk).count()
        if report.reported_user_id else 0,
        "actions": report.actions.select_related("mod")[:20],
    })


def _window(report):
    """Messages a moderator may see for a report: newest first, never past the
    reported message (or, for whole-chat reports, the time of the report)."""
    qs = report.conversation.messages.order_by("-id")
    if report.message_id:
        return qs.filter(id__lte=report.message_id)
    return qs.filter(created_at__lte=report.created_at)


def _close(report, mod, status, note):
    report.status, report.handled_by, report.handled_at = status, mod, timezone.now()
    report.resolution_note = note
    report.save(update_fields=["status", "handled_by", "handled_at", "resolution_note"])
    # other open reports about the very same content are settled too
    same = Report.objects.filter(status=Report.OPEN, kind=report.kind).exclude(pk=report.pk)
    if report.kind == Report.POST and report.post_id:
        same = same.filter(post_id=report.post_id)
    elif report.kind == Report.COMMENT and report.comment_id:
        same = same.filter(comment_id=report.comment_id)
    elif report.kind == Report.MESSAGE and report.message_id:
        same = same.filter(message_id=report.message_id)
    else:
        return
    same.update(status=status, handled_by=mod, handled_at=report.handled_at,
                resolution_note=f"Closed together with report #{report.pk}.")


def _remove_content(report):
    if report.kind == Report.POST and report.post_id:
        post = report.post
        if post.image:
            post.image.delete(save=False)
        Post.objects.filter(pk=post.pk).delete()  # reports keep their snapshot; the FK is set to NULL
        report.post = None
        return True
    if report.kind == Report.COMMENT and report.comment_id and not report.comment.removed:
        if not report.comment.is_deleted:
            Post.objects.filter(pk=report.comment.post_id, comment_count__gt=0).update(comment_count=F("comment_count") - 1)
        Comment.objects.filter(pk=report.comment_id).update(removed=True, is_deleted=True, body="")
        return True
    if report.kind == Report.MESSAGE and report.message_id and not report.message.removed:
        msg = report.message
        if msg.image:
            msg.image.delete(save=False)
        Message.objects.filter(pk=msg.pk).update(removed=True, text="", image="")
        return True
    return False


@mod_required
def users(request):
    q = request.GET.get("q", "").strip()
    qs = User.objects.select_related("profile", "restriction").order_by("-date_joined")
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(alias__icontains=q) | Q(username__icontains=q)
                       | Q(profile__name__icontains=q))
    qs = qs.annotate(open_reports=Count("reports_received", filter=Q(reports_received__status=Report.OPEN)),
                     removed_by_mod=Exists(_mod_deletions().filter(target_user=OuterRef("pk"))))
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "moderation/users.html", {"page": page, "q": q})


def _can_restrict(mod, target):
    if mod.pk == target.pk or not target.is_active:
        return False
    if is_admin(target):
        return False
    return is_admin(mod) or not is_mod(target)


def _mod_deletions():
    """A moderator deleting an account is always logged; deleting your own account never is."""
    return ModAction.objects.filter(action="deleted account")


@mod_required
def user_detail(request, user_id):
    target = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    me = request.user
    restriction = Restriction.objects.filter(user=target).first()
    can_restrict = _can_restrict(me, target)

    if request.method == "POST" and request.POST.get("action") == "birth_date":
        if not can_restrict or get_profile(target) is None:
            raise Http404
        try:
            born = datetime.date.fromisoformat(request.POST.get("birth_date", ""))
        except ValueError:
            messages.error(request, "Enter the date as YYYY-MM-DD.")
            return redirect("mod_user", user_id=user_id)
        if born > timezone.localdate():
            messages.error(request, "A date of birth can't be in the future.")
            return redirect("mod_user", user_id=user_id)
        profile = target.profile
        old = profile.birth_date
        profile.birth_date = born
        profile.record_edit("age")
        profile.save()
        log(me, "corrected date of birth", f"{old} → {born}", target)
        messages.success(request, f"Date of birth updated. They are now {profile.age}"
                                  f"{' (under-18 group)' if profile.is_minor else ''}.")
        if profile.age < settings.MIN_AGE:
            messages.error(request, f"They are under {settings.MIN_AGE}, the minimum age. "
                                    "Consider limiting the account or hiding the profile.")
        return redirect("mod_user", user_id=user_id)

    if request.method == "POST":
        if not can_restrict:
            raise Http404
        raw = request.POST.get("request_limit", "").strip()
        try:
            limit = None if raw == "" else max(0, min(100, int(raw)))
        except ValueError:
            messages.error(request, "Connection requests per day must be a number (or empty for the normal limit).")
            return redirect("mod_user", user_id=user_id)
        r = restriction or Restriction(user=target)
        before = _describe(restriction)
        r.request_limit = limit
        for flag, _ in Restriction.FLAGS:
            setattr(r, flag, request.POST.get(flag) == "on")
        r.note = request.POST.get("note", "").strip()[:2000]
        r.updated_by = me
        if r.is_active:
            r.save()
        elif r.pk:
            r.delete()
        if r.no_matchmaking:
            MatchSession.objects.filter(user=target, status=MatchSession.SEARCHING).update(
                status=MatchSession.CANCELLED, ended_at=timezone.now())
        after = _describe(r if r.is_active else None)
        log(me, "changed limits", f"{before} → {after}" + (f"\nNote: {r.note}" if r.note else ""), target)
        messages.success(request, "Limits saved." if r.is_active else "All limits removed.")
        return redirect("mod_user", user_id=user_id)

    profile = get_profile(target)
    conns_qs = Connection.objects.filter(Q(user1=target) | Q(user2=target)).select_related("user1__profile", "user2__profile")
    conns = conns_qs.order_by("-created_at")[:50]
    return render(request, "moderation/user_detail.html", {
        "target": target, "profile": profile,
        "deletion": None if target.is_active else _mod_deletions().filter(target_user=target).select_related("mod").first(),
        "rows": profile_rows(profile, SELF) if profile else [],
        "privacy_labels": PRIVACY_LABELS,
        "photos": GalleryPhoto.objects.filter(user=target),
        "posts": Post.objects.filter(author=target)[:20],
        "comments": Comment.objects.filter(author=target).select_related("post").order_by("-created_at")[:20],
        "connections": [c.other(target) for c in conns],
        "connection_count": conns_qs.count(),
        # last_seen is noted at most once a minute and open pages check in every 8 s
        "online_now": bool(target.last_seen and timezone.now() - target.last_seen < datetime.timedelta(minutes=2)),
        "blocks_made": Block.objects.filter(blocker=target).count(),
        "blocks_received": Block.objects.filter(blocked=target).count(),
        "match_sessions": MatchSession.objects.filter(user=target).count(),
        "reports_against": Report.objects.filter(reported_user=target).select_related("reporter__profile")[:30],
        "reports_filed": Report.objects.filter(reporter=target).select_related("reported_user__profile")[:30],
        "restriction": restriction, "flags": Restriction.FLAGS, "can_restrict": can_restrict,
        "default_limit": settings.PROFILE_REQUESTS_PER_DAY,
        "target_is_mod": is_mod(target), "target_is_admin": is_admin(target),
        "actions": ModAction.objects.filter(target_user=target).select_related("mod")[:30],
    })


@mod_required
def delete_user(request, user_id):
    target = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    if not _can_restrict(request.user, target):
        raise Http404
    summary = account_summary(target)
    if request.method == "POST":
        remove = request.POST.get("remove_content") == "on"
        delete_account(target, remove_content=remove)
        log(request.user, "deleted account",
            ("Also deleted " if remove else "Kept ") + describe(summary) + ".", target)
        messages.success(request, "Account deleted." + (" Their posts, comments and chats were deleted too."
                                                        if remove else " Their posts, comments and chats were kept."))
        return redirect("mod_user", user_id=target.pk)
    return render(request, "moderation/delete_user.html", {
        "target": target, "profile": get_profile(target), "summary": summary,
    })


@mod_required
def allowlist(request):
    me = request.user
    domain = settings.ALLOWED_EMAIL_DOMAIN
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            email = request.POST.get("email", "").strip().lower()
            note = request.POST.get("note", "").strip()[:200]
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, "Enter a valid email address.")
                return redirect("mod_allowlist")
            if domain and email_allowed(email):
                messages.info(request, f"{email} is a college address; it can always sign in.")
                return redirect("mod_allowlist")
            entry, created = AllowedEmail.objects.get_or_create(email=email, defaults={"note": note, "added_by": me})
            if created:
                log(me, "allowed email", email + (f" ({note})" if note else ""),
                    User.objects.filter(email__iexact=email).first())
                messages.success(request, f"{email} can now sign in.")
            else:
                messages.info(request, f"{email} is already allowed.")
        elif action == "remove":
            entry = get_object_or_404(AllowedEmail, pk=request.POST.get("entry"))
            entry.delete()
            log(me, "removed allowed email", entry.email, User.objects.filter(email__iexact=entry.email).first())
            still = may_sign_in(entry.email)
            messages.success(request, f"{entry.email} removed from the list."
                             + ("" if still else " If they're signed in, they'll be signed out on their next click."))
        else:
            return HttpResponseBadRequest()
        return redirect("mod_allowlist")

    entries = list(AllowedEmail.objects.select_related("added_by"))
    accounts = {u.email.lower(): u for u in User.objects.filter(email__in=[e.email for e in entries])
                .select_related("profile")}
    for e in entries:
        e.account = accounts.get(e.email)
    # signed-up accounts that the college-email rule now keeps out
    blocked = []
    if domain:
        outside = (User.objects.filter(is_active=True).exclude(email="")
                   .exclude(email__iendswith="@" + domain).exclude(email__iendswith="." + domain)
                   .select_related("profile").order_by("-date_joined")[:200])
        blocked = [u for u in outside if not may_sign_in(u.email)]
    return render(request, "moderation/allowlist.html", {
        "entries": entries, "blocked": blocked, "domain": domain,
    })


def _describe(r):
    if r is None or not r.is_active:
        return "no limits"
    parts = [label for flag, label in Restriction.FLAGS if getattr(r, flag)]
    if r.request_limit is not None:
        parts.insert(0, f"{r.request_limit} requests/day")
    return ", ".join(parts)


@mod_required
def mod_profile_photo(request, user_id):
    profile = get_profile(get_object_or_404(User, pk=user_id))
    if profile is None or not profile.photo:
        raise Http404
    return serve_image(profile.photo)


@mod_required
def mod_gallery_photo(request, pk):
    return serve_image(get_object_or_404(GalleryPhoto, pk=pk).image)


@mod_required
def mod_message_image(request, pk):
    msg = get_object_or_404(Message, pk=pk)
    # only photos inside the context window a report allows moderators to see
    largest = max(settings.MOD_CONTEXT_SIZES)
    for report in Report.objects.filter(conversation_id=msg.conversation_id).select_related("conversation"):
        if msg.pk in set(_window(report).values_list("id", flat=True)[:largest]):
            return serve_image(msg.image)
    raise Http404


@admin_required
def admin_page(request):
    me = request.user
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            email = request.POST.get("email", "").strip().lower()
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, "Enter a valid email address.")
                return redirect("mod_admin")
            grant, created = ModeratorGrant.objects.get_or_create(email=email, defaults={"added_by": me})
            if created:
                log(me, "added moderator", email, User.objects.filter(email__iexact=email).first())
                messages.success(request, f"{email} is now a moderator.")
            else:
                messages.info(request, f"{email} is already a moderator.")
        elif action == "remove":
            grant = get_object_or_404(ModeratorGrant, pk=request.POST.get("grant"))
            log(me, "removed moderator", grant.email, User.objects.filter(email__iexact=grant.email).first())
            grant.delete()
            messages.success(request, f"{grant.email} is no longer a moderator.")
        else:
            return HttpResponseBadRequest()
        return redirect("mod_admin")

    grants = list(ModeratorGrant.objects.select_related("added_by__profile"))
    accounts = {u.email.lower(): u for u in User.objects.filter(email__in=[g.email for g in grants])}
    for g in grants:
        g.account = accounts.get(g.email)
    admins = list(User.objects.filter(Q(is_superuser=True) | Q(email__in=settings.ADMIN_EMAILS)).select_related("profile"))
    return render(request, "moderation/admin.html", {
        "grants": grants, "admins": admins, "env_admins": sorted(settings.ADMIN_EMAILS),
        "actions": ModAction.objects.select_related("mod__profile", "target_user__profile")[:60],
    })
