"""Who is matched first (activity groups, then longest waiting), Fast queue and the search timer."""
import re
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.tests import make_user
from live.matcher import Matcher

from .models import MatchSession
from .services import try_match
from .views import _clock


def search(user, started_ago, seen_ago=None, **fields):
    """An open search by `user` that started `started_ago`; they were last on the site `seen_ago`
    (None = never)."""
    session = MatchSession.objects.create(user=user, expires_at=timezone.now() + timedelta(hours=24), **fields)
    MatchSession.objects.filter(pk=session.pk).update(created_at=timezone.now() - started_ago)
    User.objects.filter(pk=user.pk).update(last_seen=timezone.now() - seen_ago if seen_ago is not None else None)
    session.refresh_from_db()
    return session


@override_settings(MATCH_REVEAL_SECONDS=0)
class MatchOrderTests(TestCase):
    def test_active_groups_first_then_whoever_started_first(self):
        m = timedelta(minutes=1)
        waiting = {
            "never seen, waiting longest": search(make_user("Ira Bose", "female"), 300 * m, None),
            "seen 17 min ago": search(make_user("Jaya Sen", "female"), 80 * m, 17 * m),
            "seen 12 min ago": search(make_user("Kavya Iyer", "female"), 70 * m, 12 * m),
            "seen 7 min ago, started 30 min ago": search(make_user("Lata Roy", "female"), 30 * m, 7 * m),
            "seen 9 min ago, started 60 min ago": search(make_user("Mira Das", "female"), 60 * m, 9 * m),
            "seen 5:10 ago, started 100 min ago": search(make_user("Nila Paul", "female"), 100 * m, 5 * m + timedelta(seconds=10)),
            "seen 4:50 ago, started 5 min ago": search(make_user("Ojas Rao", "female"), 5 * m, 4 * m + timedelta(seconds=50)),
        }
        expected = [
            "seen 4:50 ago, started 5 min ago",          # 0-4:59
            "seen 5:10 ago, started 100 min ago",        # 5-9:59, the oldest search in it
            "seen 9 min ago, started 60 min ago",
            "seen 7 min ago, started 30 min ago",
            "seen 12 min ago",                           # 10-14:59
            "seen 17 min ago",                           # 15-19:59
            "never seen, waiting longest",               # everyone else, first come first served
        ]
        got = []
        for i in range(len(expected)):
            newcomer = search(make_user(f"Searcher {i}", "male"), timedelta(0), timedelta(0))
            matched = try_match(newcomer)
            got.append(next(k for k, s in waiting.items() if s.user_id == matched.matched_with_id))
        self.assertEqual(got, expected)

    def test_the_background_matcher_goes_in_the_same_order(self):
        m = timedelta(minutes=1)
        idle = search(make_user("Asha Rao", "female"), 60 * m, 45 * m)       # waiting longest, but away
        here = search(make_user("Bilal Khan", "male"), 20 * m, timedelta(0))
        also_here = search(make_user("Chitra Nair", "female"), 10 * m, 2 * m)
        Matcher().run_once()
        here.refresh_from_db()
        idle.refresh_from_db()
        self.assertEqual(here.matched_with_id, also_here.user_id)            # the two people who are here
        self.assertEqual(idle.status, MatchSession.SEARCHING)                 # used to get Bilal: oldest first


@override_settings(MATCH_REVEAL_SECONDS=0, MATCH_FAST_QUEUE_MINUTES=5)
class FastQueueTests(TestCase):
    def setUp(self):
        self.me = make_user("Arjun Das", "male", 21)
        self.client.force_login(self.me)

    def start(self, **data):
        self.client.post(reverse("match_start"), {"min_age": 18, "max_age": 25, **data})
        return MatchSession.objects.get(user=self.me, status=MatchSession.SEARCHING)

    def age(self, session, minutes):
        MatchSession.objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))

    def test_fast_queue_turns_off_the_filters_and_keeps_the_place_in_line(self):
        mine = self.start(genders="female", use_personality="1", personality="introvert")
        her = search(make_user("Riya Sen", "female", 27, personality="extrovert"), timedelta(minutes=30), timedelta(0))
        self.assertEqual(try_match(mine).status, MatchSession.SEARCHING)      # too old and an extrovert

        page = self.client.get(reverse("match")).content.decode()
        self.assertRegex(page, r'id="fast-queue" data-wait="\d+" hidden')     # not before 5 minutes
        self.client.post(reverse("match_fast"))
        mine.refresh_from_db()
        self.assertEqual((mine.personality, mine.fast_queued_at), ("introvert", None))

        self.age(mine, 6)
        mine.refresh_from_db()
        started = mine.created_at
        self.assertIn('id="fast-queue" data-wait="0" >', self.client.get(reverse("match")).content.decode())
        res = self.client.post(reverse("match_fast"))
        mine.refresh_from_db()
        self.assertEqual(mine.matched_with_id, her.user_id)
        self.assertRedirects(res, reverse("chat_room", args=[mine.conversation_id]))
        self.assertEqual((mine.genders, mine.personality, mine.looking_for, mine.years), ("", "", "", ""))
        self.assertEqual((mine.min_age, mine.max_age), (18, 60))
        self.assertIsNotNone(mine.fast_queued_at)
        self.assertEqual(mine.created_at, started)                           # same place in line

    def test_under_18s_stay_with_under_18s(self):
        self.me = make_user("Kabir Jain", "male", 17)
        self.client.force_login(self.me)
        mine = self.start(min_age=16, max_age=17, use_years="1", years="1")
        self.age(mine, 6)
        self.client.post(reverse("match_fast"))
        mine.refresh_from_db()
        self.assertEqual((mine.min_age, mine.max_age, mine.years), (16, 17, ""))

    def test_no_button_when_nothing_is_left_to_turn_off(self):
        mine = self.start(min_age=18, max_age=60)
        self.age(mine, 6)
        self.assertNotIn('id="fast-queue"', self.client.get(reverse("match")).content.decode())
        self.client.post(reverse("match_fast"))
        mine.refresh_from_db()
        self.assertIsNone(mine.fast_queued_at)
        # and once it's on, it doesn't show again
        other = self.start_after_cancel(genders="female")
        self.age(other, 6)
        self.client.post(reverse("match_fast"))
        page = self.client.get(reverse("match")).content.decode()
        self.assertNotIn('id="fast-queue"', page)
        self.assertIn("On, filters off", page)

    def start_after_cancel(self, **data):
        self.client.post(reverse("match_cancel"))
        return self.start(**data)

    def test_a_timer_counts_up_instead_of_the_expiry(self):
        mine = self.start()
        self.age(mine, 6)
        page = self.client.get(reverse("match")).content.decode()
        self.assertRegex(page, r'Searching for <b id="search-elapsed" data-elapsed="36\d">6:0\d</b>')
        self.assertNotIn("expires", page)
        self.assertEqual([_clock(s) for s in (0, 7, 299, 300, 3723)], ["0:00", "0:07", "4:59", "5:00", "1:02:03"])
