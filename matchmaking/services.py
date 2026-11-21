"""Matching. The WebSocket service tries new searches every couple of seconds (live/matcher.py);
pages also try when a search starts and while they wait without a socket. Both are safe at once:
try_match locks the two searches before pairing them."""
from datetime import timedelta

from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from accounts.privacy import blocked_ids, connected_ids
from chat.models import Conversation
from chat.services import create_match_conversation, member_q
from live.publish import push_badges, push_match

from .models import MatchSession

PENDING = "match_pending"   # session key: the search this browser is showing the "matching" screen for


def expire_stale():
    now = timezone.now()
    stale = MatchSession.objects.filter(status=MatchSession.SEARCHING, expires_at__lte=now)
    users = list(stale.values_list("user_id", flat=True))
    if users:
        stale.update(status=MatchSession.EXPIRED, ended_at=now)
        push_badges(*users)   # their "searching" dot goes out


def excluded_partner_ids(user):
    """People `user` must not be matched with right now: existing connections,
    anyone matched with in the cooldown window, and open matchmaking chats."""
    since = timezone.now() - timedelta(days=settings.MATCH_COOLDOWN_DAYS)
    recent = Conversation.objects.filter(member_q(user), origin=Conversation.ORIGIN_MATCH).filter(
        created_at__gte=since) | Conversation.objects.filter(
        member_q(user), tier=Conversation.MATCH, is_active=True)
    ids = set(connected_ids(user)) | blocked_ids(user)
    for u1, u2 in recent.values_list("user1_id", "user2_id"):
        ids.add(u2 if u1 == user.pk else u1)
    ids.add(user.pk)
    return ids


def looking_set(session, profile):
    """What a searcher is looking for in this search: the options they ticked plus their profile's
    answer. Empty = the filter is off, so they're open to anything."""
    picked = set(session.values("looking_for"))
    if picked and profile.looking_for:
        picked.add(profile.looking_for)
    return picked


def _accepts(session, profile):
    """Age range plus the one-way filters (personality, year). Someone who hasn't answered a
    question is never ruled out by it."""
    if not (session.min_age <= profile.age <= session.max_age):
        return False
    for field, value in (("personality", profile.personality), ("years", profile.year)):
        wanted = session.values(field)
        if wanted and value and value not in wanted:
            return False
    return True


def wants_gender(session, profile):
    """Is this search open to `profile`'s gender? An empty choice means any gender."""
    genders = session.values("genders")
    return not genders or profile.gender in genders


def reach_of(session):
    """"All NITs" only counts while it's switched on; until then every search stays on its campus."""
    return session.reach if settings.ALL_NITS_LIVE else MatchSession.CAMPUS


def reach_q(user, reach):
    """The searches someone at `user`'s NIT with this reach could meet: their own NIT, plus (for an
    All NITs search) everyone else who chose All NITs."""
    q = Q(user__campus=user.campus)
    if reach == MatchSession.ALL and settings.ALL_NITS_LIVE:
        q |= Q(reach=MatchSession.ALL)
    return q


def same_reach(a, b):
    """Same NIT, or both chose All NITs."""
    if a.user.campus == b.user.campus:
        return True
    return reach_of(a) == MatchSession.ALL and reach_of(b) == MatchSession.ALL


def compatible(a, b):
    if not same_reach(a, b):
        return False
    pa, pb = a.user.profile, b.user.profile
    if pa.is_minor != pb.is_minor:  # under-18s are matched only with under-18s
        return False
    if not (wants_gender(a, pb) and wants_gender(b, pa)):
        return False
    # "looking for" works both ways: they need one answer in common, unless either is open to anything
    la, lb = looking_set(a, pa), looking_set(b, pb)
    if la and lb and not la & lb:
        return False
    return _accepts(a, pb) and _accepts(b, pa)


def try_match(session):
    """Try to pair `session` with a compatible searcher: first those who've been on the site in the
    last MATCH_ACTIVE_MINUTES, longest-waiting first; then everyone else, longest-waiting first.

    A search only joins in once it's MATCH_REVEAL_SECONDS old, so the "matching" screen always
    shows for a moment, even when someone fits straight away."""
    expire_stale()
    session.refresh_from_db()
    if session.status != MatchSession.SEARCHING:
        return session
    now = timezone.now()
    settled = now - timedelta(seconds=settings.MATCH_REVEAL_SECONDS)
    if session.created_at > settled:
        return session
    excluded = excluded_partner_ids(session.user)
    if MatchSession.objects.filter(pk=session.pk, user__restriction__no_matchmaking=True).exists():
        MatchSession.objects.filter(pk=session.pk).update(status=MatchSession.CANCELLED, ended_at=timezone.now())
        session.refresh_from_db()
        return session
    around = now - timedelta(minutes=settings.MATCH_ACTIVE_MINUTES)
    candidates = (MatchSession.objects.active().filter(created_at__lte=settled).exclude(user_id__in=excluded)
                  .filter(reach_q(session.user, reach_of(session)))
                  .exclude(user__restriction__no_matchmaking=True)
                  .annotate(away=Case(When(user__last_seen__gte=around, then=Value(0)), default=Value(1),
                                      output_field=IntegerField()))
                  .select_related("user__profile").order_by("away", "created_at")[:500])
    for cand in candidates:
        if not compatible(session, cand):
            continue
        try:
            with transaction.atomic():
                # lock both rows in a fixed order to avoid deadlocks between two pollers
                locked = list(MatchSession.objects.select_for_update()
                              .filter(pk__in=[session.pk, cand.pk]).order_by("pk"))
                if len(locked) != 2 or any(s.status != MatchSession.SEARCHING for s in locked):
                    session.refresh_from_db()
                    if session.status != MatchSession.SEARCHING:
                        return session
                    continue
                conv = create_match_conversation(session.user, cand.user)
                now = timezone.now()
                MatchSession.objects.filter(pk=session.pk).update(
                    status=MatchSession.MATCHED, matched_with=cand.user, conversation=conv, ended_at=now)
                MatchSession.objects.filter(pk=cand.pk).update(
                    status=MatchSession.MATCHED, matched_with=session.user, conversation=conv, ended_at=now)
                push_match(session.user_id, conv.pk)   # sent once this commits
                push_match(cand.user_id, conv.pk)
        except OperationalError:
            break  # lock contention – the next poll will try again
        session.refresh_from_db()
        return session
    return session
