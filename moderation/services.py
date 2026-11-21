"""Deleting an account on a moderator's say-so.

The user row stays behind as a nameless "Deleted account" so that comment threads,
other people's chats and the moderator log keep their shape; everything that
identifies the person (email, name, profile, photos, Google link) is removed.
"""
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.db import transaction
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from accounts.models import Block, Connection, ConnectionRequest, GalleryPhoto, Profile
from chat.models import Conversation, Message, PriorityRequest
from chat.services import system_message
from feed.models import Comment, Post
from live.publish import close_user
from matchmaking.models import MatchSession
from notifications.models import Notification

from .models import AllowedEmail, FormerAccount, ModeratorGrant, Restriction


def _conversations(user):
    return Conversation.objects.filter(Q(user1=user) | Q(user2=user))


def account_summary(user):
    profile = Profile.objects.filter(user=user).first()
    return {
        "posts": Post.objects.filter(author=user).count(),
        "comments": Comment.objects.filter(author=user, is_deleted=False).count(),
        "chats": _conversations(user).count(),
        "messages": Message.objects.filter(sender=user).count(),
        "photos": GalleryPhoto.objects.filter(user=user).count() + int(bool(profile and profile.photo)),
    }


def describe(summary):
    return (f"{summary['posts']} posts, {summary['comments']} comments, "
            f"{summary['chats']} chats ({summary['messages']} messages they sent)")


def remember_former_account(user, profile):
    """Keep only the email, date of birth and moderator limits of an account being deleted, so the
    limits come back if the same person signs up again (see moderation.signals)."""
    email = (user.email or "").strip().lower()
    if not email:
        return
    restriction = Restriction.objects.filter(user=user).first()
    limits = ({f: getattr(restriction, f) for f in FormerAccount.LIMIT_FIELDS}
              if restriction and restriction.is_active else {})
    former, _ = FormerAccount.objects.get_or_create(email=email)
    if profile and profile.birth_date:
        former.birth_date = profile.birth_date
    former.limits = limits
    former.deletions += 1
    former.last_deleted_at = timezone.now()
    former.last_account = user
    former.save()


def delete_account(user, *, remove_content=False):
    """Delete `user`. With remove_content, their posts, comments and every chat they were in go too;
    otherwise those stay, shown as written by "Deleted account", and their chats end."""
    files = []   # (storage, name) pairs, removed only once the database change has committed
    keep = lambda f: f and files.append((f.storage, f.name))  # noqa: E731

    with transaction.atomic():
        convs = _conversations(user)
        if remove_content:
            for post in Post.objects.filter(author=user).only("id", "image"):
                keep(post.image)
            Post.objects.filter(author=user).delete()          # other people's comments under them go too
            # comments are blanked rather than deleted, so other people's replies stay threaded
            mine = Comment.objects.filter(author=user, is_deleted=False)
            for post_id, n in mine.values_list("post_id").annotate(n=Count("id")).order_by():
                Post.objects.filter(pk=post_id).update(comment_count=Greatest(F("comment_count"), Value(n)) - n)
            mine.update(is_deleted=True, body="")
            for msg in Message.objects.filter(conversation__in=convs).exclude(image="").only("id", "image"):
                keep(msg.image)
            convs.delete()                                     # both sides of every chat, with their photos
        else:
            # the other person keeps the history, but nothing more can be sent
            for conv in convs.filter(is_active=True):
                conv.is_active = False
                conv.save(update_fields=["is_active"])
                system_message(conv, "This account was deleted.")
            PriorityRequest.objects.filter(Q(from_user=user) | Q(to_user=user),
                                           status=PriorityRequest.PENDING).delete()

        profile = Profile.objects.filter(user=user).first()
        remember_former_account(user, profile)
        if profile:
            keep(profile.photo)
        for photo in GalleryPhoto.objects.filter(user=user):
            keep(photo.image)
        GalleryPhoto.objects.filter(user=user).delete()
        Profile.objects.filter(user=user).delete()
        Connection.objects.filter(Q(user1=user) | Q(user2=user)).delete()
        ConnectionRequest.objects.filter(Q(from_user=user) | Q(to_user=user)).delete()
        Block.objects.filter(Q(blocker=user) | Q(blocked=user)).delete()
        MatchSession.objects.filter(user=user).delete()
        Restriction.objects.filter(user=user).delete()
        Notification.objects.filter(Q(recipient=user) | Q(actor=user)).delete()
        if user.email:   # a deleted account mustn't keep moderator or allowlist access for its address
            ModeratorGrant.objects.filter(email__iexact=user.email).delete()
            AllowedEmail.objects.filter(email__iexact=user.email).delete()
        SocialAccount.objects.filter(user=user).delete()       # signing in with Google again makes a new account
        EmailAddress.objects.filter(user=user).delete()

        user.username = f"deleted-{user.pk}"
        user.email = user.first_name = user.last_name = ""
        user.is_active = user.is_staff = user.is_superuser = False   # inactive users are signed out everywhere
        user.set_unusable_password()
        user.save()
        close_user(user.pk)   # its open pages are disconnected once this commits

        def remove_files():
            for storage, name in files:
                try:
                    storage.delete(name)
                except Exception:  # a missing file shouldn't undo the deletion
                    pass
        transaction.on_commit(remove_files)
