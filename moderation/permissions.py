"""Who is an admin / moderator, and what limits apply to an account.
Everything is cached on the user object for the length of one request."""
from django.conf import settings

from .models import ModeratorGrant, Restriction


def is_admin(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return user.is_superuser or (user.email or "").lower() in settings.ADMIN_EMAILS


def is_mod(user):
    if not getattr(user, "is_authenticated", False):
        return False
    cached = getattr(user, "_is_mod", None)
    if cached is None:
        cached = is_admin(user) or ModeratorGrant.objects.filter(email__iexact=user.email or "-").exists()
        user._is_mod = cached
    return cached


_NO_LIMITS = Restriction()


def restriction_for(user):
    """The account's Restriction, or an empty one (no limits)."""
    if not getattr(user, "is_authenticated", False):
        return _NO_LIMITS
    cached = getattr(user, "_restriction_cache", None)
    if cached is None:
        cached = Restriction.objects.filter(user=user).first() or _NO_LIMITS
        user._restriction_cache = cached
    return cached


def hidden_profile_ids(viewer):
    """Accounts a moderator hid from everyone. Cached on the viewer for the request."""
    cached = getattr(viewer, "_hidden_profiles", None)
    if cached is None:
        cached = frozenset(Restriction.objects.filter(profile_hidden=True).values_list("user_id", flat=True))
        if viewer is not None:
            try:
                viewer._hidden_profiles = cached
            except AttributeError:
                pass
    return cached


def request_limit(user):
    limit = restriction_for(user).request_limit
    return settings.PROFILE_REQUESTS_PER_DAY if limit is None else limit
