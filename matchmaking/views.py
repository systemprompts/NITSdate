from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.profile_fields import GENDER_CHOICES, LOOKING_FOR_CHOICES, PERSONALITY_CHOICES, YEAR_CHOICES

from moderation.permissions import restriction_for

from .models import MatchSession
from .services import expire_stale, try_match

FILTERS = [
    ("looking_for", "Here for", LOOKING_FOR_CHOICES,
     "You're matched with people here for at least one of the same things. Your profile's \u201cLooking for\u201d answer "
     "is always included."),
    ("personality", "Personality type", PERSONALITY_CHOICES,
     "Only people with one of these personalities. People who haven't said still count."),
    ("years", "Currently studying", YEAR_CHOICES,
     "Only people in one of these years. People who haven't said still count."),
]


def age_bounds(profile):
    """Allowed age range for this person's group."""
    if profile.is_minor:
        return settings.MIN_AGE, settings.ADULT_AGE - 1
    return settings.ADULT_AGE, 60


def _current(user):
    expire_stale()
    return MatchSession.objects.active().filter(user=user).first()


PENDING = "match_pending"   # session key: the search this browser is showing the "matching" screen for


def _first_check_ms(current):
    """When the searching page should first ask for news: right after a new search becomes matchable,
    otherwise on the normal 4-second beat."""
    if current is None:
        return 4000
    left = (current.created_at + timedelta(seconds=settings.MATCH_REVEAL_SECONDS) - timezone.now()).total_seconds()
    return int(left * 1000) + 300 if left > 0 else 4000


@login_required
def match_home(request):
    me = request.user
    current = _current(me)
    if current:
        current = try_match(current)
    else:
        # the search started in this browser may have been matched by the other person before this page loaded
        pending = request.session.pop(PENDING, None)
        current = MatchSession.objects.filter(pk=pending, user=me).first() if pending else None
    if current and current.status == MatchSession.MATCHED and current.conversation_id:
        request.session.pop(PENDING, None)
        messages.success(request, "It's a match! Say hi 👋")
        return redirect("chat_room", pk=current.conversation_id)
    if current and current.status != MatchSession.SEARCHING:
        current = None
    last = MatchSession.objects.filter(user=me).first()
    lo, hi = age_bounds(me.profile)
    selected = {key: (last.values(key) if last else []) for key, *_ in FILTERS}
    from_profile = {"looking_for": me.profile.looking_for}
    history = MatchSession.objects.filter(user=me).exclude(status=MatchSession.SEARCHING)[:6]
    return render(request, "matchmaking/match.html", {
        "current": current,
        "filters": [{"key": k, "label": label, "choices": choices, "help": help_text, "selected": selected[k],
                     "on": bool(selected[k]), "from_profile": from_profile.get(k, "")}
                    for k, label, choices, help_text in FILTERS],
        "min_age": min(max(last.min_age, lo), hi) if last else lo,
        "max_age": min(max(last.max_age, lo), hi) if last else min(30, hi),
        "age_floor": lo,
        "age_ceiling": hi,
        "is_minor": me.profile.is_minor,
        "gender_choices": GENDER_CHOICES,
        "selected_genders": [],                        # every search starts at Any
        "history": history,
        "searching_now": MatchSession.objects.active().exclude(user=me).count(),
        "search_hours": settings.MATCH_SEARCH_HOURS,
        "cooldown_days": settings.MATCH_COOLDOWN_DAYS,
        "connect_after": settings.MATCH_CONNECT_MESSAGES,
        "mm_blocked": restriction_for(me).no_matchmaking,
        "first_check_ms": _first_check_ms(current),
    })


def _chosen_genders(posted):
    """The genders picked for this search; "any", nothing, or every gender all mean anyone.
    It has nothing to do with "I like" on the profile."""
    picked = [g for g, _ in GENDER_CHOICES if g in posted]
    if "any" in posted or len(picked) == len(GENDER_CHOICES):
        return ""
    return ",".join(picked)


@login_required
@require_POST
def start(request):
    me = request.user
    if restriction_for(me).no_matchmaking:
        messages.error(request, "A moderator has turned off matchmaking for your account.")
        return redirect("match")
    if _current(me):
        return redirect("match")
    lo, hi = age_bounds(me.profile)
    try:
        min_age = int(request.POST.get("min_age", lo))
        max_age = int(request.POST.get("max_age", hi))
    except ValueError:
        messages.error(request, "Ages must be numbers.")
        return redirect("match")
    min_age, max_age = min(max(lo, min_age), hi), min(max(lo, max_age), hi)
    if min_age > max_age:
        messages.error(request, "The minimum age can't be above the maximum age.")
        return redirect("match")
    chosen = {"genders": _chosen_genders(request.POST.getlist("genders"))}
    for key, _, choices, _ in FILTERS:
        on = bool(request.POST.get(f"use_{key}"))   # an unticked filter means "anyone"
        picked = set(request.POST.getlist(key)) if on else set()
        if on and key == "looking_for" and me.profile.looking_for:
            picked.add(me.profile.looking_for)       # your profile's answer always counts
        chosen[key] = ",".join(c for c, _ in choices if c in picked)
    session = MatchSession.objects.create(
        user=me, min_age=min_age, max_age=max_age,
        expires_at=timezone.now() + timedelta(hours=settings.MATCH_SEARCH_HOURS), **chosen)
    request.session[PENDING] = session.pk
    session = try_match(session)   # waits for MATCH_REVEAL_SECONDS, so normally the searching page shows first
    if session.status == MatchSession.MATCHED:
        request.session.pop(PENDING, None)
        messages.success(request, "It's a match! Say hi 👋")
        return redirect("chat_room", pk=session.conversation_id)
    return redirect("match")


@login_required
@require_POST
def cancel(request):
    current = _current(request.user)
    if current:
        current.status = MatchSession.CANCELLED
        current.ended_at = timezone.now()
        current.save(update_fields=["status", "ended_at"])
        messages.info(request, "Matchmaking cancelled.")
    return redirect("match")


@login_required
def status(request):
    current = MatchSession.objects.filter(user=request.user).first()
    if current and current.status == MatchSession.SEARCHING:
        current = try_match(current)
    if current is None:
        return JsonResponse({"status": "none"})
    data = {"status": current.status}
    if current.status == MatchSession.MATCHED and current.conversation_id:
        data["url"] = reverse("chat_room", args=[current.conversation_id])
        request.session.pop(PENDING, None)
    return JsonResponse(data)
