from django.conf import settings

from .models import ConnectionRequest


def nav(request):
    user = request.user
    ctx = {"DEV_LOGIN": settings.DEV_LOGIN}
    if not user.is_authenticated or not user.has_profile:
        return ctx
    from chat.services import unread_by_conversation
    from matchmaking.models import MatchSession
    from moderation.models import Report
    from moderation.permissions import is_admin, is_mod, request_limit, restriction_for

    unread = {"match": 0, "inbox": 0, "priority": 0}
    for tier, n in unread_by_conversation(user).values():
        unread[tier] += n
    ctx.update({
        "nav_unread": unread,
        "nav_unread_total": sum(unread.values()),
        "nav_requests": ConnectionRequest.objects.filter(to_user=user, status=ConnectionRequest.PENDING,
                                                         source=ConnectionRequest.SOURCE_PROFILE).count(),
        "nav_searching": MatchSession.objects.active().filter(user=user).exists(),
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
        ctx["mod_open_reports"] = Report.objects.filter(status=Report.OPEN).count()
    return ctx
