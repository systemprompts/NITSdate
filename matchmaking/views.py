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
    ("looking_for", "Looking for", LOOKING_FOR_CHOICES,
     "You're matched with people who want at least one of the same things. Your profile's answer is always included."),
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


@login_required
def match_home(request):
    me = request.user
    current = _current(me)
    if current:
        current = try_match(current)
        if current.status == MatchSession.MATCHED:
            messages.success(request, "It's a match! Say hi 👋")
            return redirect("chat_room", pk=current.conversation_id)
    last = MatchSession.objects.filter(user=me).first()
    lo, hi = age_bounds(me.profile)
    selected = {key: (last.values(key) if last else []) for key, *_ in FILTERS}
    from_profile = {"looking_for": me.profile.looking_for}
    history = MatchSession.objects.filter(user=me).exclude(status=MatchSession.SEARCHING)[:6]
    likes = dict(GENDER_CHOICES)
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
        "likes_labels": [likes[g] for g in me.profile.likes_list],
        "history": history,
        "searching_now": MatchSession.objects.active().exclude(user=me).count(),
        "search_hours": settings.MATCH_SEARCH_HOURS,
        "cooldown_days": settings.MATCH_COOLDOWN_DAYS,
        "connect_after": settings.MATCH_CONNECT_MESSAGES,
        "mm_blocked": restriction_for(me).no_matchmaking,
    })


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
    chosen = {}
    for key, _, choices, _ in FILTERS:
        on = bool(request.POST.get(f"use_{key}"))   # an unticked filter means "anyone"
        picked = set(request.POST.getlist(key)) if on else set()
        if on and key == "looking_for" and me.profile.looking_for:
            picked.add(me.profile.looking_for)       # your profile's answer always counts
        chosen[key] = ",".join(c for c, _ in choices if c in picked)
    session = MatchSession.objects.create(
        user=me, min_age=min_age, max_age=max_age,
        expires_at=timezone.now() + timedelta(hours=settings.MATCH_SEARCH_HOURS), **chosen)
    session = try_match(session)
    if session.status == MatchSession.MATCHED:
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
    return JsonResponse(data)
