"""Who may see a post. A Campus post is for its author's NIT only; an All NITs post is for everyone
once settings.ALL_NITS_LIVE is on. Moderators can open any post (reports link to them)."""
from django.conf import settings
from django.db.models import Q
from django.http import Http404

from .models import Post


def visible_q(user, prefix=""):
    """Posts `user` may see, as a filter (prefix e.g. "post__" for comments)."""
    q = Q(**{f"{prefix}audience": Post.CAMPUS, f"{prefix}campus": user.campus})
    if settings.ALL_NITS_LIVE:
        q |= Q(**{f"{prefix}audience": Post.ALL})
    return q


def can_see(user, post):
    if post.audience == Post.ALL:
        if settings.ALL_NITS_LIVE:
            return True
    elif post.campus == user.campus:
        return True
    from moderation.permissions import is_mod
    return is_mod(user)


def check_visible(user, post):
    """404 rather than 403, so other NITs can't even tell the post exists."""
    if not can_see(user, post):
        raise Http404
    return post
