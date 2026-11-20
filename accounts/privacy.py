"""Who can see what.

relation(owner, viewer) -> "self" | "blocked" | "connection" | "stranger"

"blocked" means the owner hid their profile from the viewer: nothing is visible.

A field is visible when:
  * viewer is the owner, or
  * privacy == connections and they are connected, or
  * privacy == public and (they are connected, or the profile isn't locked).
The name is never hidden by the profile lock: when it is hidden by its own
privacy setting the randomized alias is shown instead.
"""
from django.db.models import Q

from moderation.permissions import hidden_profile_ids

from .models import Block, Connection, Profile
from .profile_fields import CONNECTIONS, FIELDS, ONLY_ME, PRIVACY_LABELS

SELF, CONNECTION, STRANGER, BLOCKED = "self", "connection", "stranger", "blocked"


def connected_ids(user):
    """Ids of everyone `user` is connected with. Cached on the user object for the request."""
    if not getattr(user, "is_authenticated", False):
        return frozenset()
    cached = getattr(user, "_connected_ids", None)
    if cached is None:
        rows = Connection.objects.filter(Q(user1=user) | Q(user2=user)).values_list("user1_id", "user2_id")
        cached = frozenset(b if a == user.pk else a for a, b in rows)
        user._connected_ids = cached
    return cached


def profile_blockers(viewer):
    """Ids of people whose profile `viewer` may not see: they hid it from the viewer,
    or a moderator hid it from everyone. Cached for the request."""
    if not getattr(viewer, "is_authenticated", False):
        return frozenset()
    cached = getattr(viewer, "_profile_blockers", None)
    if cached is None:
        cached = frozenset(Block.objects.filter(blocked=viewer, profile=True).values_list("blocker_id", flat=True))
        cached = cached | hidden_profile_ids(viewer)
        viewer._profile_blockers = cached
    return cached


def _pair_blocks(a, b):
    return Block.objects.filter(Q(blocker=a, blocked=b) | Q(blocker=b, blocked=a))


def messages_blocked(a, b):
    """Either of them blocked messages: the chat is paused for both."""
    return _pair_blocks(a, b).filter(messages=True).exists()


def any_block(a, b):
    return _pair_blocks(a, b).filter(Q(messages=True) | Q(profile=True)).exists()


# A block made inside a matchmaking chat that is still blind must never show up
# on profile pages or profile requests: that would reveal who the partner is.
NOT_BLIND = Q(conversation__isnull=True) | ~Q(conversation__tier="match")


def visible_block(a, b):
    """Any block between a and b except ones made in a still-blind matchmaking chat."""
    return _pair_blocks(a, b).filter(NOT_BLIND).filter(Q(messages=True) | Q(profile=True)).exists()


def blocked_ids(user):
    """Everyone with a block in either direction (used to keep them out of matchmaking)."""
    rows = Block.objects.filter(Q(blocker=user) | Q(blocked=user)).filter(Q(messages=True) | Q(profile=True))
    return {b if a == user.pk else a for a, b in rows.values_list("blocker_id", "blocked_id")}


def forget_connections(user):
    if hasattr(user, "_connected_ids"):
        del user._connected_ids


def are_connected(a, b):
    return b.pk in connected_ids(a)


def relation(owner, viewer):
    if viewer is not None and viewer.pk == owner.pk:
        return SELF
    if viewer is not None and owner.pk in profile_blockers(viewer):
        return BLOCKED
    if viewer is not None and are_connected(viewer, owner):
        return CONNECTION
    return STRANGER


def get_profile(user):
    try:
        return user.profile
    except Profile.DoesNotExist:
        return None


def field_visible(profile, key, rel):
    if rel == SELF:
        return True
    if rel == BLOCKED:
        return False
    privacy = profile.privacy_of(key)
    if privacy == ONLY_ME:
        return False
    if privacy == CONNECTIONS:
        return rel == CONNECTION
    if rel == CONNECTION or key == "name":
        return True
    return profile.profile_visible


DELETED_NAME = "Deleted account"


def display_name(owner, viewer):
    if not owner.is_active:  # a moderator deleted the account; its row stays so threads and chats keep their shape
        return DELETED_NAME
    profile = get_profile(owner)
    if profile is None:
        return owner.alias
    rel = relation(owner, viewer)
    return profile.name if field_visible(profile, "name", rel) else owner.alias


def name_is_hidden(owner, viewer):
    """True when `viewer` sees the randomized alias instead of the real name."""
    if not owner.is_active:
        return False
    profile = get_profile(owner)
    if profile is None:
        return True
    return not field_visible(profile, "name", relation(owner, viewer))


def visible_gender(owner, viewer):
    """The owner's gender if `viewer` may see it (respects gender privacy and the profile lock), else None."""
    profile = get_profile(owner)
    if profile is None:
        return None
    return profile.gender if field_visible(profile, "gender", relation(owner, viewer)) else None


def photo_visible(owner, viewer):
    profile = get_profile(owner)
    if profile is None or not profile.photo:
        return False
    return field_visible(profile, "photo", relation(owner, viewer))


def profile_rows(profile, rel):
    """Rows for the profile page. Hidden values are replaced by None but the
    row (and its (i) edit counter) is still shown."""
    rows = []
    for f in FIELDS:
        visible = field_visible(profile, f.key, rel)
        value = profile.display_value(f.key) if visible else None
        if rel != SELF and visible and f.key != "photo" and value in ("", [], None):
            continue  # nothing to show for an empty optional field
        rows.append({
            "field": f,
            "key": f.key,
            "visible": visible,
            "value": value,
            "raw": profile.raw_value(f.key),
            "privacy": profile.privacy_of(f.key),
            "privacy_label": PRIVACY_LABELS[profile.privacy_of(f.key)],
            "edits": profile.edits_of(f.key),
            "last_edited": profile.last_edited_of(f.key),
        })
    return rows


def same_age_group(a, b):
    """Under-18s are only paired (matchmaking or connections) with other under-18s."""
    pa, pb = get_profile(a), get_profile(b)
    return pa is not None and pb is not None and pa.is_minor == pb.is_minor


AGE_GROUP_MESSAGE = "Connections are only between students in the same age group (under 18, or 18 and over)."


def gallery_visible(photo, profile, rel):
    """Same rules as a profile field, using the photo's own privacy setting."""
    if rel == SELF:
        return True
    if rel == BLOCKED or photo.privacy == ONLY_ME:
        return False
    if photo.privacy == CONNECTIONS:
        return rel == CONNECTION
    return rel == CONNECTION or profile.profile_visible
