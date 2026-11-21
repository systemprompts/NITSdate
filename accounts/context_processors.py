from django.conf import settings

from .models import ConnectionRequest


def badge_counts(user):
    """The numbers on the nav badges. Pages render them; after that the WebSocket service pushes
    them when they change (live/consumers.py), or the page asks chat.views.badges if it can't connect."""
    from chat.services import unread_by_conversation
    from matchmaking.models import MatchSession
    from moderation.models import Report
    from moderation.permissions import is_mod
    from notifications.services import unread_count as unread_notifications

    tiers = {"match": 0, "inbox": 0, "priority": 0}
    for tier, n in unread_by_conversation(user).values():
        tiers[tier] += n
    counts = {
        "tiers": tiers,
        "messages": sum(tiers.values()),
        # every request waiting for an answer, from a profile or a matchmaking chat (same as People → Requests)
        "requests": ConnectionRequest.objects.filter(to_user=user, status=ConnectionRequest.PENDING).count(),
        "notifications": unread_notifications(user),
        "searching": MatchSession.objects.active().filter(user=user).exists(),
    }
    if is_mod(user):
        counts["reports"] = Report.objects.filter(status=Report.OPEN).count()
    return counts


def nav(request):
    user = request.user
    ctx = {"DEV_LOGIN": settings.DEV_LOGIN, "LIVE_WS_URL": settings.LIVE_WS_URL}
    if not user.is_authenticated or not user.has_profile:
        return ctx
    from moderation.permissions import is_admin, is_mod, request_limit, restriction_for

    counts = badge_counts(user)
    ctx.update({
        "nav_unread": counts["tiers"],
        "nav_unread_total": counts["messages"],
        "nav_requests": counts["requests"],
        "nav_notifications": counts["notifications"],
        "nav_waiting": counts["messages"] + counts["requests"] + counts["notifications"],
        "nav_searching": counts["searching"],
    })
    limits = restriction_for(user)
    ctx["limits"] = limits
    if limits.is_active:
        labels = [label[0].lower() + label[1:] for flag, label in limits.FLAGS if getattr(limits, flag)]
        if limits.request_limit is not None:
            labels.insert(0, f"{request_limit(user)} connection requests a day" if request_limit(user)
                          else "no connection requests")
        ctx["limit_labels"] = labels
    if is_mod(user):
        ctx["is_mod"] = True
        ctx["is_admin"] = is_admin(user)
        ctx["mod_open_reports"] = counts.get("reports", 0)
    return ctx
