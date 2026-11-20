import re

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Connection, ConnectionRequest
from accounts.tests import POST_FORM_RE, make_user
from chat.models import Conversation, Message
from chat.services import connect_users, create_match_conversation
from feed.models import Comment, Post, PostVote
from matchmaking.models import MatchSession

from .models import ModAction, ModeratorGrant, Report, Restriction


class ModerationTests(TestCase):
    def setUp(self):
        self.alice = make_user("Alice Mod", "female", 20, "male")
        self.bob = make_user("Bob Bad", "male", 21, "female")
        self.mod = make_user("Mona Mod", "female", 22, "male")
        ModeratorGrant.objects.create(email=self.mod.email)
        self.admin = make_user("Adi Admin", "male", 23, "female")
        self.admin.is_superuser = True
        self.admin.save()

    def as_(self, user):
        self.client.force_login(user)

    def report(self, url, reason="harassment"):
        return self.client.post(url, {"reason": reason, "details": "please check"})

    # ---- reporting -------------------------------------------------------------
    def test_report_every_kind(self):
        post = Post.objects.create(author=self.bob, title="bad post", body="nasty")
        comment = Comment.objects.create(post=post, author=self.bob, body="mean comment")
        reply = Comment.objects.create(post=post, author=self.bob, parent=comment, body="mean reply")
        conv = connect_users(self.alice, self.bob)
        msg = Message.objects.create(conversation=conv, sender=self.bob, stage=conv.tier, text="rude message")
        self.as_(self.alice)
        for url in (reverse("report_post", args=[post.pk]), reverse("report_comment", args=[comment.pk]),
                    reverse("report_comment", args=[reply.pk]), reverse("report_message", args=[msg.pk]),
                    reverse("report_profile", args=[self.bob.pk]), reverse("report_chat", args=[conv.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)
            self.assertEqual(self.report(url).status_code, 302, url)
        self.assertEqual(Report.objects.filter(reporter=self.alice, reported_user=self.bob).count(), 6)
        self.assertEqual(Report.objects.get(kind="message").snapshot, "rude message")
        self.assertEqual(Report.objects.get(post=post, kind="post").snapshot, "bad post\n\nnasty")
        # duplicates are ignored, own content can't be reported
        self.report(reverse("report_post", args=[post.pk]))
        self.assertEqual(Report.objects.filter(kind="post").count(), 1)
        mine = Post.objects.create(author=self.alice, title="mine")
        self.assertEqual(self.client.get(reverse("report_post", args=[mine.pk])).status_code, 404)

    def test_blind_chat_report_does_not_reveal_identity(self):
        conv = create_match_conversation(self.alice, self.bob)
        msg = Message.objects.create(conversation=conv, sender=self.bob, stage=conv.tier, text="hey")
        self.as_(self.alice)
        for url in (reverse("report_message", args=[msg.pk]), reverse("report_chat", args=[conv.pk])):
            html = self.client.get(url).content.decode()
            self.assertNotIn("Bob Bad", html)
            self.assertNotIn(self.bob.alias, html)
            self.assertNotIn(f"/u/{self.bob.pk}/", html)
            self.assertIn(conv.nickname_for(self.bob), html)
        room = self.client.get(reverse("chat_room", args=[conv.pk])).content.decode()
        self.assertNotIn(f"/u/{self.bob.pk}/", room)

    # ---- access ------------------------------------------------------------------
    def test_only_mods_and_admins_get_in(self):
        pages = [reverse("mod_reports"), reverse("mod_users"), reverse("mod_user", args=[self.bob.pk])]
        self.as_(self.alice)
        for url in pages + [reverse("mod_admin")]:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.as_(self.mod)
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(reverse("mod_admin")).status_code, 404)  # mods aren't admins
        self.as_(self.admin)
        self.assertEqual(self.client.get(reverse("mod_admin")).status_code, 200)

    def test_admin_adds_and_removes_mods_by_email(self):
        self.as_(self.admin)
        self.client.post(reverse("mod_admin"), {"action": "add", "email": "Future.Mod@cse.nits.ac.in"})
        self.assertTrue(ModeratorGrant.objects.filter(email="future.mod@cse.nits.ac.in").exists())
        later = make_user("Future Mod")
        later.email = "future.mod@cse.nits.ac.in"
        later.save()
        self.as_(later)
        self.assertEqual(self.client.get(reverse("mod_reports")).status_code, 200)
        self.as_(self.admin)
        grant = ModeratorGrant.objects.get(email="future.mod@cse.nits.ac.in")
        self.client.post(reverse("mod_admin"), {"action": "remove", "grant": grant.pk})
        self.as_(later)
        self.assertEqual(self.client.get(reverse("mod_reports")).status_code, 404)

    @override_settings(ADMIN_EMAILS={"boss@nits.ac.in"})
    def test_admin_from_env(self):
        boss = make_user("Boss Env")
        boss.email = "boss@nits.ac.in"
        boss.save()
        self.as_(boss)
        self.assertEqual(self.client.get(reverse("mod_admin")).status_code, 200)

    def test_mod_sees_everything_about_a_user(self):
        p = self.bob.profile
        p.bio = "secret bio"
        p.set_privacy("bio", "me")
        p.save()
        self.as_(self.mod)
        html = self.client.get(reverse("mod_user", args=[self.bob.pk])).content.decode()
        self.assertIn(self.bob.email, html)
        self.assertIn("secret bio", html)

    # ---- report review ---------------------------------------------------------------
    def test_message_report_context_10_20_100(self):
        conv = connect_users(self.alice, self.bob)
        msgs = [Message.objects.create(conversation=conv, sender=self.bob if i % 2 else self.alice,
                                       stage=conv.tier, text=f"line {i}") for i in range(150)]
        reported = msgs[121]  # one of Bob's messages
        Message.objects.create(conversation=conv, sender=self.bob, stage=conv.tier, text="after the report")
        self.as_(self.alice)
        self.report(reverse("report_message", args=[reported.pk]))
        rep = Report.objects.get()
        self.as_(self.mod)
        for n in (10, 20, 100):
            html = self.client.get(reverse("mod_report", args=[rep.pk]) + f"?n={n}").content.decode()
            shown = re.findall(r"line (\d+)", html)
            lines = sorted({int(x) for x in shown})
            self.assertEqual(lines, list(range(122 - n, 122)), n)  # the n messages up to the reported one
            self.assertNotIn("after the report", html)

    def test_remove_post_comment_and_message(self):
        post = Post.objects.create(author=self.bob, title="spam spam")
        comment = Comment.objects.create(post=Post.objects.create(author=self.alice, title="ok"), author=self.bob, body="bad")
        conv = connect_users(self.alice, self.bob)
        msg = Message.objects.create(conversation=conv, sender=self.bob, stage=conv.tier, text="bad msg")
        self.as_(self.alice)
        self.report(reverse("report_post", args=[post.pk]))
        self.report(reverse("report_comment", args=[comment.pk]))
        self.report(reverse("report_message", args=[msg.pk]))
        self.as_(self.mod)
        for rep in Report.objects.all():
            self.client.post(reverse("mod_report", args=[rep.pk]), {"action": "remove", "note": "against rules"})
        self.assertFalse(Post.objects.filter(pk=post.pk).exists())
        comment.refresh_from_db()
        msg.refresh_from_db()
        self.assertTrue(comment.removed and comment.body == "")
        self.assertTrue(msg.removed and msg.text == "")
        self.assertEqual(Report.objects.filter(status="resolved").count(), 3)
        self.assertEqual(ModAction.objects.filter(mod=self.mod).count(), 3)
        self.as_(self.alice)
        self.assertIn("removed by a moderator", self.client.get(reverse("chat_room", args=[conv.pk])).content.decode())

    # ---- limits --------------------------------------------------------------------
    def limit(self, **values):
        data = {"request_limit": values.pop("request_limit", ""), "note": "test"}
        data.update({k: "on" for k, v in values.items() if v})
        self.as_(self.mod)
        return self.client.post(reverse("mod_user", args=[self.bob.pk]), data)

    def test_request_limit_zero_and_custom(self):
        self.limit(request_limit=0)
        self.as_(self.bob)
        self.client.post(reverse("send_request", args=[self.alice.pk]))
        self.assertFalse(ConnectionRequest.objects.exists())
        self.limit(request_limit=1)
        self.as_(self.bob)
        self.client.post(reverse("send_request", args=[self.alice.pk]))
        self.client.post(reverse("send_request", args=[self.admin.pk]))
        self.assertEqual(ConnectionRequest.objects.count(), 1)

    def test_no_matchmaking(self):
        self.as_(self.bob)
        self.client.post(reverse("match_start"), {"min_age": 18, "max_age": 30})
        self.limit(no_matchmaking=True)
        self.assertFalse(MatchSession.objects.filter(user=self.bob, status="searching").exists())  # cancelled
        self.as_(self.bob)
        self.client.post(reverse("match_start"), {"min_age": 18, "max_age": 30})
        self.as_(self.alice)
        self.client.post(reverse("match_start"), {"min_age": 18, "max_age": 30})
        self.assertFalse(Conversation.objects.exists())

    def test_no_posting_and_no_interaction(self):
        post = Post.objects.create(author=self.alice, title="hello")
        self.limit(no_posting=True, no_interaction=True)
        self.as_(self.bob)
        self.client.post(reverse("new_post"), {"title": "new"})
        self.assertEqual(Post.objects.count(), 1)
        resp = self.client.post(reverse("vote_post", args=[post.pk]), {"value": 1}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.status_code, 403)
        self.client.post(reverse("add_comment", args=[post.pk]), {"body": "hi"})
        self.assertFalse(PostVote.objects.exists() or Comment.objects.exists())
        html = self.client.get("/").content.decode()
        self.assertNotIn("Write a post", html)
        self.assertIn("A moderator has limited your account", html)

    def test_no_messaging(self):
        conv = connect_users(self.alice, self.bob)
        self.limit(no_messaging=True)
        self.as_(self.bob)
        resp = self.client.post(reverse("chat_send", args=[conv.pk]), {"text": "hi"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn('id="composer"', self.client.get(reverse("chat_room", args=[conv.pk])).content.decode())

    def test_profile_hidden_even_from_connections(self):
        Connection.objects.create(user1_id=min(self.alice.pk, self.bob.pk), user2_id=max(self.alice.pk, self.bob.pk))
        self.limit(profile_hidden=True)
        self.as_(self.alice)
        self.assertEqual(self.client.get(reverse("profile", args=[self.bob.pk])).status_code, 404)
        self.as_(self.mod)  # moderators still see everything in the mod area
        self.assertContains(self.client.get(reverse("mod_user", args=[self.bob.pk])), self.bob.email)

    def test_hidden_profile_cannot_receive_connection_requests(self):
        self.limit(profile_hidden=True)
        self.as_(self.alice)
        self.assertEqual(self.client.post(reverse("send_request", args=[self.bob.pk])).status_code, 404)
        self.assertFalse(ConnectionRequest.objects.filter(from_user=self.alice, to_user=self.bob).exists())

    def test_clearing_limits_deletes_row_and_mods_cannot_limit_admins(self):
        self.limit(no_posting=True)
        self.assertTrue(Restriction.objects.filter(user=self.bob).exists())
        self.limit()
        self.assertFalse(Restriction.objects.filter(user=self.bob).exists())
        self.as_(self.mod)
        resp = self.client.post(reverse("mod_user", args=[self.admin.pk]), {"no_posting": "on"})
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Restriction.objects.filter(user=self.admin).exists())

    def test_mods_correct_birth_date_and_see_minors_first(self):
        import datetime
        teen = make_user("Teen Reported", "male", 17, "female")
        Post.objects.create(author=self.bob, title="adult post")
        old = Report.objects.create(kind="profile", reason="spam", reporter=self.alice, reported_user=self.bob)
        newer = Report.objects.create(kind="profile", reason="spam", reporter=self.alice, reported_user=self.bob)
        minor = Report.objects.create(kind="profile", reason="fake", reporter=self.alice, reported_user=teen)
        Report.objects.filter(pk=minor.pk).update(created_at=old.created_at - datetime.timedelta(days=1))
        self.as_(self.mod)
        html = self.client.get(reverse("mod_reports")).content.decode()
        order = [int(x) for x in re.findall(r'href="/mod/reports/(\d+)/"', html)]
        self.assertEqual(order[0], minor.pk)  # oldest, but involves an under-18 so it's first
        self.assertIn("Under 18 involved", html)
        self.client.post(reverse("mod_user", args=[teen.pk]), {"action": "birth_date", "birth_date": "2000-05-05"})
        teen.profile.refresh_from_db()
        self.assertEqual(str(teen.profile.birth_date), "2000-05-05")
        self.assertFalse(teen.profile.is_minor)
        self.assertTrue(ModAction.objects.filter(action="corrected date of birth", target_user=teen).exists())

    def test_all_new_forms_have_csrf_tokens(self):
        post = Post.objects.create(author=self.bob, title="x")
        conv = connect_users(self.alice, self.bob)
        msg = Message.objects.create(conversation=conv, sender=self.bob, stage=conv.tier, text="m")
        self.as_(self.alice)
        self.report(reverse("report_message", args=[msg.pk]))
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice)
        pages = [reverse("report_post", args=[post.pk]), reverse("report_profile", args=[self.bob.pk])]
        client2 = Client(enforce_csrf_checks=True)
        client2.force_login(self.admin)
        rep = Report.objects.get()
        for c, urls in ((client, pages), (client2, [reverse("mod_report", args=[rep.pk]), reverse("mod_user", args=[self.bob.pk]),
                                                   reverse("mod_admin")])):
            for url in urls:
                forms = POST_FORM_RE.findall(c.get(url).content.decode())
                self.assertTrue(forms, url)
                for body in forms:
                    self.assertIn("csrfmiddlewaretoken", body, url)


@override_settings(MEDIA_ROOT="/tmp/nitsdate-test-media")
class ReviewFixTests(TestCase):
    """Regression tests for issues found in code review."""

    def setUp(self):
        self.a = make_user("Anon A", "female", 20, "male")
        self.b = make_user("Anon B", "male", 21, "female")
        self.mod = make_user("Mod Person", "female", 22, "male")
        ModeratorGrant.objects.create(email=self.mod.email)

    def blind_chat(self):
        conv = create_match_conversation(self.a, self.b)
        Message.objects.bulk_create([Message(conversation=conv, sender=u, stage=conv.tier, text="x")
                                     for u in (self.a, self.b) for _ in range(10)])
        return conv

    def test_blind_chat_requests_and_blocks_stay_off_profile_pages(self):
        conv = self.blind_chat()
        self.client.force_login(self.a)
        self.client.post(reverse("chat_connect", args=[conv.pk]))        # match-source request
        self.client.post(reverse("block_in_chat", args=[conv.pk]), {"kind": "messages", "on": "1"})
        self.client.get(reverse("people"))  # consume the "Messages blocked" confirmation banner
        html = self.client.get(reverse("profile", args=[self.b.pk])).content.decode()
        for leak in ("Request sent", "Messages blocked", "Unblock messages", "Accept request"):
            self.assertNotIn(leak, html)
        self.client.force_login(self.b)
        html = self.client.get(reverse("profile", args=[self.a.pk])).content.decode()
        self.assertNotIn("Accept request", html)
        resp = self.client.post(reverse("send_request", args=[self.a.pk]), follow=True)
        self.assertNotContains(resp, "already sent")
        self.assertNotContains(resp, "blocked")
        self.assertTrue(ConnectionRequest.objects.filter(from_user=self.b, source="profile").exists())

    def test_profile_report_page_shows_only_what_the_reporter_may_see(self):
        p = self.b.profile
        p.bio = "very private bio"
        p.set_privacy("name", "connections")
        p.set_privacy("bio", "me")
        p.save()
        self.client.force_login(self.a)
        html = self.client.get(reverse("report_profile", args=[self.b.pk])).content.decode()
        self.assertNotIn("Anon B", html)
        self.assertNotIn("very private bio", html)
        self.assertIn(self.b.alias, html)
        self.client.post(reverse("report_profile", args=[self.b.pk]), {"reason": "fake"})
        self.assertIn("very private bio", Report.objects.get().snapshot)  # moderators get the full copy
        from accounts.models import Block
        Block.objects.create(blocker=self.b, blocked=self.a, profile=True)
        self.a._profile_blockers = None
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(reverse("report_profile", args=[self.b.pk])).status_code, 404)

    def test_report_next_cannot_be_javascript(self):
        post = Post.objects.create(author=self.b, title="t")
        self.client.force_login(self.a)
        html = self.client.get(reverse("report_post", args=[post.pk]) + "?next=javascript:alert(1)").content.decode()
        self.assertNotIn("javascript:", html)

    def test_chat_report_context_and_photos_stop_at_report_time(self):
        import datetime
        from accounts.tests import png_bytes
        from django.core.files.base import ContentFile
        from django.utils import timezone
        conv = connect_users(self.a, self.b)
        before = Message.objects.create(conversation=conv, sender=self.b, stage=conv.tier, text="before report")
        before.image.save("b.jpg", ContentFile(png_bytes()))
        self.client.force_login(self.a)
        self.client.post(reverse("report_chat", args=[conv.pk]), {"reason": "harassment"})
        rep = Report.objects.get()
        Message.objects.filter(pk=before.pk).update(created_at=timezone.now() - datetime.timedelta(seconds=10))
        Report.objects.filter(pk=rep.pk).update(created_at=timezone.now() - datetime.timedelta(seconds=5))
        after = Message.objects.create(conversation=conv, sender=self.b, stage=conv.tier, text="after report")
        after.image.save("a.jpg", ContentFile(png_bytes()))
        self.client.force_login(self.mod)
        html = self.client.get(reverse("mod_report", args=[rep.pk]) + "?n=100").content.decode()
        self.assertIn("before report", html)
        self.assertNotIn("after report", html)
        self.assertEqual(self.client.get(reverse("mod_message_image", args=[before.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("mod_message_image", args=[after.pk])).status_code, 404)

    def test_removing_a_post_closes_every_report_about_it(self):
        post = Post.objects.create(author=self.b, title="spam")
        other = make_user("Other Reporter", "female", 20, "male")
        for reporter in (self.a, other):
            self.client.force_login(reporter)
            self.client.post(reverse("report_post", args=[post.pk]), {"reason": "spam"})
        first = Report.objects.order_by("id").first()
        self.client.force_login(self.mod)
        self.client.post(reverse("mod_report", args=[first.pk]), {"action": "remove"})
        self.assertFalse(Post.objects.filter(pk=post.pk).exists())
        self.assertEqual(Report.objects.filter(status="open").count(), 0)

    def test_comment_count_stays_right(self):
        post = Post.objects.create(author=self.a, title="p")
        self.client.force_login(self.b)
        for body in ("one", "two", "three"):
            self.client.post(reverse("add_comment", args=[post.pk]), {"body": body})
        c1, c2 = Comment.objects.filter(body__in=["one", "two"]).order_by("id")
        self.client.post(reverse("delete_comment", args=[c1.pk]))
        self.client.post(reverse("delete_comment", args=[c1.pk]))  # double submit
        post.refresh_from_db()
        self.assertEqual(post.comment_count, 2)
        self.client.force_login(self.a)
        self.client.post(reverse("report_comment", args=[c2.pk]), {"reason": "spam"})
        self.client.force_login(self.mod)
        self.client.post(reverse("mod_report", args=[Report.objects.get().pk]), {"action": "remove"})
        post.refresh_from_db()
        self.assertEqual(post.comment_count, 1)

    @override_settings(DEV_LOGIN=True, ALLOWED_HOSTS=["*"])
    def test_public_link_test_login_cannot_reach_mods(self):
        self.client.logout()
        remote = {"HTTP_HOST": "abc.ngrok-free.app"}
        html = self.client.get(reverse("dev_login"), **remote).content.decode()
        self.assertNotIn(self.mod.email, html)
        self.assertIn(self.a.email, html)
        self.client.post(reverse("dev_login"), {"action": "login", "user_id": self.mod.pk}, **remote)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.post(reverse("dev_login"), {"action": "create", "name": "Sneaky", "email": self.mod.email.upper()}, **remote)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.post(reverse("dev_login"), {"action": "login", "user_id": self.mod.pk}, HTTP_HOST="127.0.0.1:8000")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.mod.pk)  # fine on this computer

    def test_seed_demo_includes_the_demo_admin(self):
        from django.core.management import call_command
        from io import StringIO
        call_command("seed_demo", stdout=StringIO())
        from accounts.models import User
        self.assertTrue(User.objects.get(email="admin@demo.nits.ac.in").is_superuser)

    def test_leap_day_birthday(self):
        import datetime
        p = self.a.profile
        p.birth_date = datetime.date(2008, 2, 29)
        self.assertEqual(p.adult_on, datetime.date(2026, 3, 1))


class ScoreCacheTests(TestCase):
    def test_deleting_an_account_updates_scores(self):
        author = make_user("Author Person", "female", 20, "male")
        voter = make_user("Voter Person", "male", 21, "female")
        post = Post.objects.create(author=author, title="p")
        self.client.force_login(voter)
        self.client.post(reverse("vote_post", args=[post.pk]), {"value": 1}, HTTP_X_REQUESTED_WITH="fetch")
        post.refresh_from_db()
        self.assertEqual(post.score, 1)
        voter.delete()
        post.refresh_from_db()
        self.assertEqual(post.score, 0)
