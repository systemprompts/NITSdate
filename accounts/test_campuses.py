"""Many NITs: each student's campus, campus-only feeds, the All NITs feed and matching across NITs."""
from datetime import timedelta

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.campuses import CAMPUSES, campus_of_email
from accounts.models import User
from accounts.tests import make_user
from feed.models import Comment, Post
from matchmaking.models import MatchSession
from matchmaking.services import try_match
from moderation.models import AllowedEmail, ModeratorGrant


def at(domain, name, **kwargs):
    """A student with a profile whose college email is on `domain`."""
    user = make_user(name, **kwargs)
    user.email = f"{user.username}@{domain}"
    user.campus = ""          # worked out again from the email on save
    user.save()
    return user


class CampusTests(TestCase):
    def test_campus_comes_from_the_college_email(self):
        self.assertEqual(campus_of_email("x_ug_23@cse.nits.ac.in").code, "silchar")
        self.assertEqual(campus_of_email("ab21cs@student.nitw.ac.in").code, "warangal")
        self.assertEqual(campus_of_email("someone@nitsri.net").code, "srinagar")
        self.assertEqual(campus_of_email("u20@coed.svnit.ac.in").code, "surat")
        self.assertIsNone(campus_of_email("x@evilnits.ac.in"))
        self.assertIsNone(campus_of_email("x@nitt.edu"))
        self.assertIsNone(campus_of_email(""))
        self.assertEqual(len(CAMPUSES), 27)
        self.assertEqual(len({d for c in CAMPUSES for d in c.domains}), 28)   # no domain listed twice

    def test_new_accounts_get_a_campus(self):
        self.assertEqual(User.objects.create(username="w", email="w@student.nitw.ac.in").campus, "warangal")
        AllowedEmail.objects.create(email="guest@gmail.com", campus="calicut")
        self.assertEqual(User.objects.create(username="g", email="guest@gmail.com").campus, "calicut")
        self.assertEqual(User.objects.create(username="root").campus, settings.DEFAULT_CAMPUS)

    def test_a_campus_never_changes_on_its_own(self):
        user = at("student.nitw.ac.in", "Wren Iyer")
        user.email = "wren@cse.nits.ac.in"
        user.save()
        self.assertEqual(User.objects.get(pk=user.pk).campus, "warangal")

    def test_login_page_lists_the_nits(self):
        page = self.client.get(reverse("login")).content.decode()
        self.assertIn("27 NITs can sign in", page)
        self.assertIn("NIT Warangal", page)
        self.assertNotIn("NIT Silchar.", page)   # no longer "the space for NIT Silchar."

    def test_a_student_of_any_supported_nit_gets_in(self):
        self.client.force_login(at("student.nitw.ac.in", "Wren Iyer"))
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Only students of NIT Warangal see these posts.")
        self.assertContains(page, " · nitsdate</title>")

    def test_mods_pick_the_campus_of_an_allowed_outsider(self):
        mod = make_user("Mona Das")
        ModeratorGrant.objects.create(email=mod.email)
        self.client.force_login(mod)
        self.client.post(reverse("mod_allowlist"), {"action": "add", "email": "guest@gmail.com", "campus": "warangal"})
        self.assertEqual(AllowedEmail.objects.get(email="guest@gmail.com").campus, "warangal")
        self.assertEqual(User.objects.create(username="guest", email="guest@gmail.com").campus, "warangal")


@override_settings(MEDIA_ROOT="/tmp/nitsdate-test-media")
class FeedIsolationTests(TestCase):
    def setUp(self):
        self.s = make_user("Sana Paul")                               # NIT Silchar
        self.w = at("student.nitw.ac.in", "Wren Iyer")                # NIT Warangal
        self.s_post = Post.objects.create(author=self.s, title="Silchar only")
        self.s_comment = Comment.objects.create(post=self.s_post, author=self.s, body="campus talk")
        self.w_post = Post.objects.create(author=self.w, title="Warangal only")

    def test_each_campus_feed_shows_only_its_own_posts(self):
        self.assertEqual((self.s_post.campus, self.s_post.audience), ("silchar", Post.CAMPUS))
        self.client.force_login(self.s)
        page = self.client.get("/").content.decode()
        self.assertIn("Silchar only", page)
        self.assertNotIn("Warangal only", page)
        self.client.force_login(self.w)
        page = self.client.get("/").content.decode()
        self.assertIn("Warangal only", page)
        self.assertNotIn("Silchar only", page)

    def test_other_nits_cannot_reach_a_campus_post_at_all(self):
        self.client.force_login(self.w)
        pk, cpk = self.s_post.pk, self.s_comment.pk
        for method, url, data in [
            ("get", reverse("post_detail", args=[pk]), None),
            ("get", reverse("post_image", args=[pk]), None),
            ("post", reverse("vote_post", args=[pk]), {"value": 1}),
            ("post", reverse("vote_comment", args=[cpk]), {"value": 1}),
            ("post", reverse("add_comment", args=[pk]), {"body": "hi"}),
            ("post", reverse("add_comment", args=[pk]), {"body": "hi", "parent": cpk}),
            ("get", reverse("report_post", args=[pk]), None),
            ("get", reverse("report_comment", args=[cpk]), None),
        ]:
            res = getattr(self.client, method)(url, data or {})
            self.assertEqual(res.status_code, 404, url)
        self.assertEqual(Comment.objects.filter(post=self.s_post).count(), 1)
        self.assertEqual(Post.objects.get(pk=pk).score, 0)

    def test_profiles_dont_list_another_nits_campus_posts(self):
        self.client.force_login(self.w)
        page = self.client.get(reverse("profile", args=[self.s.pk]))
        self.assertEqual(page.status_code, 200)            # profiles stay viewable across NITs
        self.assertContains(page, "NIT Silchar")           # with their NIT
        self.assertNotContains(page, "Silchar only")

    def test_moderators_can_open_any_post(self):
        mod = at("student.nitw.ac.in", "Mira Rao")
        ModeratorGrant.objects.create(email=mod.email)
        self.client.force_login(mod)
        self.assertEqual(self.client.get(reverse("post_detail", args=[self.s_post.pk])).status_code, 200)

    def test_all_nits_is_coming_soon(self):
        self.client.force_login(self.s)
        page = self.client.get(reverse("all_nits"))
        self.assertContains(page, "All NITs is coming soon")
        self.assertNotContains(page, "Warangal only")
        composer = self.client.get(reverse("new_post")).content.decode()
        self.assertIn('value="all" disabled', composer)
        self.client.post(reverse("new_post"), {"title": "trying all", "audience": "all"})
        post = Post.objects.get(title="trying all")
        self.assertEqual((post.audience, post.campus), (Post.CAMPUS, "silchar"))

    @override_settings(ALL_NITS_LIVE=True)
    def test_all_nits_feed_when_live(self):
        self.client.force_login(self.s)
        self.client.post(reverse("new_post"), {"title": "hello every NIT", "audience": "all"})
        post = Post.objects.get(title="hello every NIT")
        self.assertEqual(post.audience, Post.ALL)

        self.client.force_login(self.w)
        everyone = self.client.get(reverse("all_nits")).content.decode()
        self.assertIn("hello every NIT", everyone)
        self.assertIn("NIT Silchar", everyone)                       # the author's NIT next to their name
        self.assertNotIn("Silchar only", everyone)
        self.assertNotIn("hello every NIT", self.client.get("/").content.decode())   # not in campus feeds
        self.assertEqual(self.client.get(reverse("post_detail", args=[post.pk])).status_code, 200)
        self.client.post(reverse("add_comment", args=[post.pk]), {"body": "hi from Warangal"})
        self.assertTrue(Comment.objects.filter(post=post, author=self.w).exists())
        self.client.force_login(self.s)
        self.assertNotIn("hello every NIT", self.client.get("/").content.decode())


@override_settings(MATCH_REVEAL_SECONDS=0)
class MatchReachTests(TestCase):
    def setUp(self):
        self.s = make_user("Sana Paul", gender="female", likes="male")
        self.s2 = make_user("Sid Roy", gender="male", likes="female")
        self.w = at("student.nitw.ac.in", "Wren Iyer", gender="male", likes="female")

    def search(self, user, reach=MatchSession.CAMPUS):
        return MatchSession.objects.create(user=user, reach=reach, min_age=18, max_age=30,
                                           expires_at=timezone.now() + timedelta(hours=1))

    def test_campus_searches_stay_on_campus(self):
        mine = self.search(self.s)
        self.search(self.w)
        self.assertEqual(try_match(mine).status, MatchSession.SEARCHING)
        self.search(self.s2)
        self.assertEqual(try_match(mine).matched_with, self.s2)

    def test_all_nits_needs_both_and_needs_it_live(self):
        mine = self.search(self.s, MatchSession.ALL)
        self.search(self.w, MatchSession.ALL)
        self.assertEqual(try_match(mine).status, MatchSession.SEARCHING)        # coming soon: campus only
        with self.settings(ALL_NITS_LIVE=True):
            self.assertEqual(try_match(mine).matched_with, self.w)

    @override_settings(ALL_NITS_LIVE=True)
    def test_one_side_campus_only_never_crosses(self):
        mine = self.search(self.s, MatchSession.ALL)
        self.search(self.w, MatchSession.CAMPUS)
        self.assertEqual(try_match(mine).status, MatchSession.SEARCHING)
        self.search(self.s2, MatchSession.CAMPUS)                                 # same NIT is always fine
        self.assertEqual(try_match(mine).matched_with, self.s2)

    def test_match_page_offers_all_nits_as_coming_soon(self):
        self.search(self.w)                                   # someone at another NIT is searching
        self.client.force_login(self.s)
        page = self.client.get(reverse("match")).content.decode()
        self.assertIn("Only NIT Silchar", page)
        self.assertIn('value="all" disabled', page)
        self.assertIn("0 other people are searching at NIT Silchar", page)
        self.client.post(reverse("match_start"), {"min_age": 18, "max_age": 30, "reach": "all"})
        self.assertEqual(MatchSession.objects.get(user=self.s).reach, MatchSession.CAMPUS)

    @override_settings(ALL_NITS_LIVE=True)
    def test_match_page_when_live(self):
        self.search(self.w, MatchSession.ALL)
        self.client.force_login(self.s)
        page = self.client.get(reverse("match")).content.decode()
        self.assertNotIn('value="all" disabled', page)
        self.assertIn("1 across All NITs", page)
        self.client.post(reverse("match_start"), {"min_age": 18, "max_age": 30, "reach": "all"})
        self.assertEqual(MatchSession.objects.get(user=self.s).reach, MatchSession.ALL)
