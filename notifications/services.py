"""Who hears about what. Each person chooses, on the notifications page, whether they want
upvote, comment and reply notifications; nobody is notified about their own actions or by
someone they blocked."""
from datetime import timedelta

from django.utils import timezone

from accounts.models import Block
from live.publish import push_badges

from .models import Notification

PREFERENCE = {
    Notification.POST_UPVOTE: "notify_upvotes",
    Notification.COMMENT_UPVOTE: "notify_upvotes",
    Notification.COMMENT: "notify_comments",
    Notification.REPLY: "notify_replies",
}
KEEP_READ_DAYS = 60


def notify(recipient, actor, kind, post, comment=None):
    if recipient is None or recipient.pk == actor.pk or not recipient.is_active:
        return
    if not getattr(recipient, PREFERENCE[kind]):
        return
    if Block.objects.filter(blocker=recipient, blocked=actor).exists():
        return
    if kind in Notification.UPVOTES:
        # one per voter and target, however often they toggle the vote
        Notification.objects.get_or_create(recipient=recipient, actor=actor, kind=kind, post=post, comment=comment)
    else:
        Notification.objects.create(recipient=recipient, actor=actor, kind=kind, post=post, comment=comment)


def withdraw_upvote(recipient, actor, kind, post, comment=None):
    """The vote was taken back before they saw it: drop the notification too."""
    removed, _ = Notification.objects.filter(recipient=recipient, actor=actor, kind=kind, post=post,
                                             comment=comment, read_at=None).delete()
    if removed:
        push_badges(recipient.pk)


def unread_count(user):
    """Upvotes on the same post or comment count once, the way the page groups them."""
    unread = Notification.objects.filter(recipient=user, read_at=None)
    upvoted = unread.filter(kind__in=Notification.UPVOTES).values("kind", "post_id", "comment_id").distinct().count()
    return unread.exclude(kind__in=Notification.UPVOTES).count() + upvoted


def grouped(user, limit=150):
    """Recent notifications for the page, upvotes on the same thing folded into one line."""
    items, upvotes = [], {}
    rows = (Notification.objects.filter(recipient=user)
            .select_related("actor__profile", "post", "comment")[:limit])
    for n in rows:
        if n.kind in Notification.UPVOTES:
            key = (n.kind, n.post_id, n.comment_id)
            group = upvotes.get(key)
            if group is None:
                group = upvotes[key] = {"n": n, "kind": n.kind, "post": n.post, "comment": n.comment,
                                        "count": 0, "unread": False, "at": n.created_at}
                items.append(group)
            group["count"] += 1
            group["unread"] = group["unread"] or n.read_at is None
        else:
            items.append({"n": n, "kind": n.kind, "post": n.post, "comment": n.comment, "actor": n.actor,
                          "count": 1, "unread": n.read_at is None, "at": n.created_at})
    return items


def mark_all_read(user):
    now = timezone.now()
    if Notification.objects.filter(recipient=user, read_at=None).update(read_at=now):
        push_badges(user.pk)
    Notification.objects.filter(recipient=user, read_at__lt=now - timedelta(days=KEEP_READ_DAYS)).delete()
