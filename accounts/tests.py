from datetime import timedelta
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.adapters import email_allowed
from accounts.models import Connection, ConnectionRequest, Profile, User
from accounts.privacy import CONNECTION, STRANGER, display_name, field_visible
from accounts.profile_fields import CONNECTIONS, ONLY_ME, PUBLIC
from chat.models import Conversation, Message
from feed.models import Comment, Post
from matchmaking.models import MatchSession


def make_user(name, gender="male", age=20, likes="female", visible=True, randomize=False, **extra):
    username = name.lower().replace(" ", ".")
    user = User.objects.create(username=username, email=f"{username}@cse.nits.ac.in")
    profile = Profile(user=user, name=name, gender=gender, age=age, likes=likes, profile_visible=visible, **extra)
    profile.init_meta({"name": CONNECTIONS if randomize else PUBLIC})
    profile.save()
    return user


def years_ago(years, extra_days=0):
    """ISO date string for someone who is exactly `years` old (+extra_days older)."""
    today = timezone.localdate()
    return (today.replace(year=today.year - years) - timedelta(days=extra_days)).isoformat()


def png_bytes():
    buf = BytesIO()
    Image.new("RGB", (40, 30), (200, 50, 80)).save(buf, "PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT="/tmp/nitsdate-test-media", MATCH_REVEAL_SECONDS=0)
class FlowTests(TestCase):
    def setUp(self):
        self.a = make_user("Aarav Test", "male", 21, "female")
        self.b = make_user("Bina Test", "female", 20, "male")

    def login(self, user):
        self.client.force_login(user)

    def send(self, conv, user, n):
        Message.objects.bulk_create(
            [Message(conversation=conv, sender=user, stage=conv.tier, text=f"m{i}") for i in range(n)])

    # ---- sign-up / auth ----------------------------------------------------
    def test_email_domain_rule(self):
        with override_settings(ALLOWED_EMAIL_DOMAIN=""):
            self.assertTrue(email_allowed("someone@gmail.com"))
        with override_settings(ALLOWED_EMAIL_DOMAIN="nits.ac.in"):
            self.assertTrue(email_allowed("x_ug_23@cse.nits.ac.in"))
            self.assertTrue(email_allowed("x@nits.ac.in"))
            self.assertFalse(email_allowed("x@gmail.com"))
            self.assertFalse(email_allowed("x@evilnits.ac.in"))

    def test_onboarding_required_and_randomized_name(self):
        fresh = User.objects.create(username="fresh", email="fresh@gmail.com")
        self.login(fresh)
        self.assertRedirects(self.client.get("/"), reverse("onboarding"))
        resp = self.client.post(reverse("onboarding"), {
            "name": "Fresh Person", "randomize_name": "on", "gender": "female", "birth_date": years_ago(19, 40),
            "likes": ["male", "other"], "profile_visible": "on"})
        self.assertRedirects(resp, reverse("my_profile"))
        p = Profile.objects.get(user=fresh)
        self.assertEqual(p.likes, "male,other")
        self.assertEqual(p.privacy_of("name"), CONNECTIONS)
        self.assertEqual(display_name(fresh, self.a), fresh.alias)  # stranger sees the alias
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_age_required(self):
        fresh = User.objects.create(username="noage", email="noage@gmail.com")
        self.login(fresh)
        resp = self.client.post(reverse("onboarding"), {"name": "N", "gender": "male", "likes": ["female"]})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Enter your exact date of birth")
        self.assertFalse(Profile.objects.filter(user=fresh).exists())

    def test_under_16_rejected(self):
        fresh = User.objects.create(username="kid", email="kid@gmail.com")
        self.login(fresh)
        resp = self.client.post(reverse("onboarding"), {"name": "K", "gender": "male", "birth_date": years_ago(15, 300),
                                                        "likes": ["female"]})
        self.assertContains(resp, "You must be 16 or older")
        self.assertFalse(Profile.objects.filter(user=fresh).exists())

    # ---- profile privacy -------------------------------------------------------
    def test_field_privacy_and_lock(self):
        p = self.a.profile
        p.bio = "hello"
        p.set_privacy("bio", PUBLIC)
        p.set_privacy("age", CONNECTIONS)
        p.set_privacy("gender", ONLY_ME)
        p.save()
        self.assertTrue(field_visible(p, "bio", STRANGER))
        self.assertFalse(field_visible(p, "age", STRANGER))
        self.assertTrue(field_visible(p, "age", CONNECTION))
        self.assertFalse(field_visible(p, "gender", CONNECTION))
        p.profile_visible = False  # locked: public fields hidden from strangers, not from connections
        self.assertFalse(field_visible(p, "bio", STRANGER))
        self.assertTrue(field_visible(p, "bio", CONNECTION))
        self.assertFalse(field_visible(p, "name", STRANGER))  # strangers see the randomized name
        self.assertTrue(field_visible(p, "name", CONNECTION))
        p.save()
        self.assertEqual(display_name(self.a, self.b), self.a.alias)

        self.login(self.b)
        html = self.client.get(reverse("profile", args=[self.a.pk])).content.decode()
        self.assertNotIn("hello", html)
        self.assertNotIn("Aarav Test", html)
        self.assertIn(self.a.alias, html)
        self.assertIn("Locked profile", html)
        self.assertIn("Edit history", html)  # (i) buttons still visible

    def test_edit_count_ignores_privacy_changes(self):
        self.login(self.a)
        self.client.post(reverse("edit_field", args=["bio"]), {"value": "first"})
        self.client.post(reverse("edit_field", args=["bio"]), {"value": "first"})  # unchanged
        self.client.post(reverse("set_privacy", args=["bio"]), {"privacy": ONLY_ME})
        self.client.post(reverse("edit_field", args=["bio"]), {"value": "second"})
        p = Profile.objects.get(user=self.a)
        self.assertEqual(p.bio, "second")
        self.assertEqual(p.edits_of("bio"), 2)
        self.assertEqual(p.privacy_of("bio"), ONLY_ME)

    def test_profile_photo_respects_privacy(self):
        self.login(self.a)
        upload = SimpleUploadedFile("me.png", png_bytes(), content_type="image/png")
        self.client.post(reverse("edit_field", args=["photo"]), {"value": upload})
        p = Profile.objects.get(user=self.a)
        self.assertTrue(p.photo.name.endswith(".jpg"))
        p.set_privacy("photo", CONNECTIONS)
        p.save()
        self.login(self.b)
        self.assertEqual(self.client.get(reverse("profile_photo", args=[self.a.pk])).status_code, 404)
        Connection.objects.create(user1_id=min(self.a.pk, self.b.pk), user2_id=max(self.a.pk, self.b.pk))
        self.assertEqual(self.client.get(reverse("profile_photo", args=[self.a.pk])).status_code, 200)

    # ---- connection requests -------------------------------------------------
    def test_profile_requests_limited_to_10_per_day(self):
        others = [make_user(f"Other {i}", "female") for i in range(11)]
        self.login(self.a)
        for o in others:
            self.client.post(reverse("send_request", args=[o.pk]))
        self.assertEqual(ConnectionRequest.objects.filter(from_user=self.a, source="profile").count(), 10)

    def test_accepting_profile_request_creates_inbox_with_welcome(self):
        self.login(self.a)
        self.client.post(reverse("send_request", args=[self.b.pk]))
        req = ConnectionRequest.objects.get(from_user=self.a, to_user=self.b)
        self.login(self.b)
        self.client.post(reverse("request_action", args=[req.pk, "accept"]))
        conv = Conversation.objects.get()
        self.assertEqual(conv.tier, Conversation.INBOX)
        self.assertIn("let's bind strongly", conv.messages.get(sender__isnull=True).text)
        self.assertTrue(Connection.objects.exists())

    # ---- matchmaking -------------------------------------------------------------
    def start_search(self, user, **filters):
        self.login(user)
        data = {"min_age": 18, "max_age": 30, **filters}
        return self.client.post(reverse("match_start"), data)

    def test_mutual_match_and_cooldown(self):
        self.start_search(self.a)
        self.assertEqual(MatchSession.objects.get(user=self.a).status, MatchSession.SEARCHING)
        resp = self.start_search(self.b)
        conv = Conversation.objects.get()
        self.assertRedirects(resp, reverse("chat_room", args=[conv.pk]))
        self.assertEqual(conv.tier, Conversation.MATCH)
        # they leave the chat, search again: no re-match within the cooldown
        conv.is_active = False
        conv.save()
        self.start_search(self.a)
        self.start_search(self.b)
        self.assertEqual(Conversation.objects.count(), 1)
        # after 3 days they can match again
        Conversation.objects.update(created_at=timezone.now() - timedelta(days=4))
        self.login(self.a)
        self.client.get(reverse("match_status"))
        self.assertEqual(Conversation.objects.count(), 2)

    def test_existing_connections_never_matched(self):
        Connection.objects.create(user1_id=min(self.a.pk, self.b.pk), user2_id=max(self.a.pk, self.b.pk))
        self.start_search(self.a)
        self.start_search(self.b)  # perfectly compatible, but already connected
        self.assertFalse(Conversation.objects.filter(origin=Conversation.ORIGIN_MATCH).exists())
        self.assertEqual(MatchSession.objects.get(user=self.b).status, MatchSession.SEARCHING)
        stranger = make_user("Stranger Test", "female", 20, "male")
        self.start_search(stranger)  # a compatible non-connection still gets matched with a
        conv = Conversation.objects.get(origin=Conversation.ORIGIN_MATCH)
        self.assertEqual({conv.user1_id, conv.user2_id}, {self.a.pk, stranger.pk})

    def test_gender_preferences_and_filters(self):
        c = make_user("Chandan Test", "male", 22, "male")  # likes men only
        self.start_search(self.a)          # man who likes women
        self.start_search(c)
        self.assertFalse(Conversation.objects.exists())
        self.start_search(self.b, min_age=25, max_age=30)  # b is 20, a is 21 -> a outside b's range
        self.assertFalse(Conversation.objects.exists())

    def test_unanswered_questions_never_rule_anyone_out(self):
        # a only wants 3rd years; b never said her year, so she still fits
        self.start_search(self.a, use_years="1", years=["3"])
        self.start_search(self.b)
        self.assertTrue(Conversation.objects.exists())

    def test_filters_only_count_when_switched_on(self):
        Profile.objects.filter(user=self.b).update(year="2")
        self.start_search(self.a, years=["3"])              # options picked, but the filter is off
        self.assertEqual(MatchSession.objects.get(user=self.a).years, "")
        self.start_search(self.b)
        self.assertTrue(Conversation.objects.exists())

    def test_year_filter_rules_out_other_answers(self):
        Profile.objects.filter(user=self.b).update(year="2")
        self.start_search(self.a, use_years="1", years=["3"])
        self.start_search(self.b)
        self.assertFalse(Conversation.objects.exists())

    def test_looking_for_works_both_ways_and_includes_profile(self):
        Profile.objects.filter(user=self.a).update(looking_for="unsure")
        self.start_search(self.a, use_looking_for="1", looking_for=["friendship"])
        self.assertEqual(MatchSession.objects.get(user=self.a).looking_for, "friendship,unsure")
        # b wants dating (and a relationship, from her profile): nothing in common with a
        Profile.objects.filter(user=self.b).update(looking_for="relationship")
        self.start_search(self.b, use_looking_for="1", looking_for=["dating"])
        self.assertFalse(Conversation.objects.exists())
        # c's profile says "not sure yet" and she switches the filter on: a's profile answer counts too
        c = make_user("Chitra Test", "female", 20, "male", looking_for="unsure")
        self.start_search(c, use_looking_for="1")
        conv = Conversation.objects.get()
        self.assertEqual({conv.user1_id, conv.user2_id}, {self.a.pk, c.pk})

    def test_looking_for_filter_off_is_open_to_anything(self):
        Profile.objects.filter(user=self.b).update(looking_for="relationship")
        self.start_search(self.a, use_looking_for="1", looking_for=["friendship"])
        self.start_search(self.b)                           # b didn't switch the filter on
        self.assertTrue(Conversation.objects.exists())

    @override_settings(MATCH_REVEAL_SECONDS=3)
    def test_new_search_shows_matching_screen_first(self):
        self.start_search(self.a)
        resp = self.start_search(self.b)
        self.assertRedirects(resp, reverse("match"))           # not straight into a chat
        self.assertFalse(Conversation.objects.exists())
        page = self.client.get(reverse("match"))
        self.assertContains(page, 'id="searching"')
        self.assertContains(page, 'data-first-check="')
        self.assertFalse(Conversation.objects.exists())        # still too new, even though a fits
        MatchSession.objects.update(created_at=timezone.now() - timedelta(seconds=4))
        data = self.client.get(reverse("match_status")).json()
        conv = Conversation.objects.get()
        self.assertEqual(data, {"status": "matched", "url": reverse("chat_room", args=[conv.pk])})

    @override_settings(MATCH_REVEAL_SECONDS=3)
    def test_match_made_by_the_other_side_still_reaches_the_new_searcher(self):
        other = Client()                                         # a searches in their own browser
        other.force_login(self.a)
        other.post(reverse("match_start"), {"min_age": 18, "max_age": 30})
        self.start_search(self.b)
        MatchSession.objects.update(created_at=timezone.now() - timedelta(seconds=4))
        other.get(reverse("match_status"))                      # a's poll makes the match
        conv = Conversation.objects.get()
        self.assertRedirects(self.client.get(reverse("match")), reverse("chat_room", args=[conv.pk]))
        self.assertEqual(self.client.get(reverse("match")).status_code, 200)   # only once

    def test_people_active_recently_are_matched_first(self):
        early = make_user("Early Bird", "female", 20, "male")
        late = make_user("Late Comer", "female", 20, "male")
        self.start_search(early)
        self.start_search(late)
        User.objects.filter(pk=early.pk).update(last_seen=timezone.now() - timedelta(hours=1))
        User.objects.filter(pk=late.pk).update(last_seen=timezone.now() - timedelta(minutes=5))
        self.start_search(self.a)          # early has waited longer, but late was here 5 minutes ago
        conv = Conversation.objects.get()
        self.assertEqual({conv.user1_id, conv.user2_id}, {self.a.pk, late.pk})
        # nobody active left: first come, first served
        d = make_user("Dev Test", "male", 22, "female")
        self.start_search(d)
        conv = Conversation.objects.exclude(pk=conv.pk).get()
        self.assertEqual({conv.user1_id, conv.user2_id}, {d.pk, early.pk})

    def test_last_seen_is_noted_at_most_once_a_minute(self):
        self.login(self.a)
        self.client.get(reverse("my_profile"))
        self.a.refresh_from_db()
        self.assertIsNotNone(self.a.last_seen)
        half_minute_ago = timezone.now() - timedelta(seconds=30)
        User.objects.filter(pk=self.a.pk).update(last_seen=half_minute_ago)
        self.client.get(reverse("my_profile"))
        self.a.refresh_from_db()
        self.assertEqual(self.a.last_seen, half_minute_ago)
        User.objects.filter(pk=self.a.pk).update(last_seen=timezone.now() - timedelta(minutes=2))
        self.client.get(reverse("my_profile"))
        self.a.refresh_from_db()
        self.assertGreater(self.a.last_seen, half_minute_ago)

    def test_search_expires(self):
        self.start_search(self.a)
        MatchSession.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.start_search(self.b)
        self.assertFalse(Conversation.objects.exists())
        self.assertEqual(MatchSession.objects.get(user=self.a).status, MatchSession.EXPIRED)

    # ---- match chat -> inbox -> priority --------------------------------------------
    def test_full_relationship_ladder(self):
        self.start_search(self.a)
        self.start_search(self.b)
        conv = Conversation.objects.get()

        # profile is not clickable/visible inside the match chat
        self.login(self.a)
        html = self.client.get(reverse("chat_room", args=[conv.pk])).content.decode()
        self.assertNotIn(reverse("profile", args=[self.b.pk]), html)
        self.assertNotIn("Bina Test", html)

        self.send(conv, self.a, 10)
        self.send(conv, self.b, 9)
        self.client.post(reverse("chat_connect", args=[conv.pk]))
        self.assertFalse(ConnectionRequest.objects.exists())  # not unlocked yet
        self.send(conv, self.b, 1)
        self.client.post(reverse("chat_connect", args=[conv.pk]))
        req = ConnectionRequest.objects.get()
        self.assertEqual(req.source, "match")

        self.login(self.b)
        self.client.post(reverse("request_action", args=[req.pk, "accept"]))
        conv.refresh_from_db()
        self.assertEqual(conv.tier, Conversation.INBOX)
        html = self.client.get(reverse("chat_room", args=[conv.pk])).content.decode()
        self.assertIn("Archived matchmaking chat", html)
        self.assertIn("let&#x27;s bind strongly", html)

        self.send(conv, self.a, 100)
        self.send(conv, self.b, 89)  # 99 total incl. 10 from matchmaking
        self.client.post(reverse("chat_priority", args=[conv.pk]))
        self.assertFalse(conv.priority_requests.exists())
        self.send(conv, self.b, 1)
        self.client.post(reverse("chat_priority", args=[conv.pk]))
        preq = conv.priority_requests.get()
        self.login(self.a)
        self.client.post(reverse("chat_priority_action", args=[conv.pk, preq.pk, "accept"]))
        conv.refresh_from_db()
        self.assertEqual(conv.tier, Conversation.PRIORITY)
        html = self.client.get(reverse("chat_room", args=[conv.pk])).content.decode()
        self.assertIn("Archived inbox chat", html)

    def test_chat_photos_only_for_members(self):
        self.start_search(self.a)
        self.start_search(self.b)
        conv = Conversation.objects.get()
        self.login(self.a)
        upload = SimpleUploadedFile("pic.png", png_bytes(), content_type="image/png")
        resp = self.client.post(reverse("chat_send", args=[conv.pk]), {"text": "", "image": upload},
                                HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.json(), {"ok": True})
        msg = conv.messages.exclude(image="").get()
        self.assertEqual(self.client.get(reverse("chat_photo", args=[msg.pk])).status_code, 200)
        self.login(make_user("Outsider"))
        self.assertEqual(self.client.get(reverse("chat_photo", args=[msg.pk])).status_code, 404)

    # ---- posts -------------------------------------------------------------------
    def test_votes_toggle_and_nested_comments(self):
        post = Post.objects.create(author=self.a, title="Hello")
        self.login(self.b)
        url = reverse("vote_post", args=[post.pk])
        r = self.client.post(url, {"value": 1}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual((r["score"], r["mine"]), (1, 1))
        r = self.client.post(url, {"value": -1}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual((r["score"], r["mine"]), (-1, -1))
        r = self.client.post(url, {"value": -1}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual((r["score"], r["mine"]), (0, 0))

        self.client.post(reverse("add_comment", args=[post.pk]), {"body": "top"})
        top = Comment.objects.get()
        self.client.post(reverse("add_comment", args=[post.pk]), {"body": "reply", "parent": top.pk})
        reply = Comment.objects.get(parent=top)
        self.client.post(reverse("add_comment", args=[post.pk]), {"body": "deeper", "parent": reply.pk})
        self.assertEqual(Comment.objects.get(body="deeper").parent.parent, top)
        html = self.client.get(reverse("post_detail", args=[post.pk])).content.decode()
        self.assertIn("deeper", html)

    def test_author_name_depends_on_connection(self):
        hidden = make_user("Secret Name", "female", randomize=True)
        Post.objects.create(author=hidden, title="Anon post")
        self.login(self.a)
        html = self.client.get("/?sort=new").content.decode()
        self.assertIn(hidden.alias, html)
        self.assertNotIn("Secret Name", html)
        self.assertIn("(name hidden)", html)
        profile_html = self.client.get(reverse("profile", args=[hidden.pk])).content.decode()
        self.assertIn("(name hidden)", profile_html)
        self.assertNotIn("(name hidden)", self.client.get(reverse("profile", args=[self.b.pk])).content.decode())
        Connection.objects.create(user1_id=min(self.a.pk, hidden.pk), user2_id=max(self.a.pk, hidden.pk))
        html = self.client.get("/?sort=new").content.decode()
        self.assertIn("Secret Name", html)
        self.assertNotIn("(name hidden)", html)


POST_FORM_RE = __import__("re").compile(r'<form[^>]*method="post"[^>]*>(.*?)</form>', __import__("re").S | __import__("re").I)
TOKEN_RE = __import__("re").compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


class CsrfTests(TestCase):
    """Run with CSRF enforcement on, like a real browser."""

    def setUp(self):
        self.a = make_user("Csrf A", "male", 21, "female")
        self.b = make_user("Csrf B", "female", 20, "male")
        self.client = __import__("django.test", fromlist=["Client"]).Client(enforce_csrf_checks=True)

    def assert_forms_have_tokens(self, html, where):
        forms = POST_FORM_RE.findall(html)
        for body in forms:
            self.assertIn("csrfmiddlewaretoken", body, f"POST form without CSRF token on {where}")
        return len(forms)

    def make_chat(self, tier, each):
        from chat.services import connect_users, create_match_conversation
        conv = create_match_conversation(self.a, self.b)
        if tier != Conversation.MATCH:
            conv = connect_users(self.a, self.b, via_conversation=conv)
        for user in (self.a, self.b):
            Message.objects.bulk_create([Message(conversation=conv, sender=user, stage=conv.tier, text="x")
                                         for _ in range(each)])
        return conv

    def click(self, page_url, action_url):
        """Load a page, take the CSRF token from the form that posts to action_url, submit it."""
        html = self.client.get(page_url).content.decode()
        form = __import__("re").search(r'<form[^>]*action="%s"[^>]*>(.*?)</form>' % __import__("re").escape(action_url), html, __import__("re").S)
        self.assertIsNotNone(form, f"no form posting to {action_url} on {page_url}")
        token = TOKEN_RE.search(form.group(1))
        self.assertIsNotNone(token, f"form posting to {action_url} has no CSRF token")
        return self.client.post(action_url, {"csrfmiddlewaretoken": token.group(1)})

    def test_set_priority_and_connect_buttons_work(self):
        conv = self.make_chat(Conversation.INBOX, 100)
        self.client.force_login(self.a)
        room = reverse("chat_room", args=[conv.pk])
        resp = self.click(room, reverse("chat_priority", args=[conv.pk]))
        self.assertEqual(resp.status_code, 302)
        preq = conv.priority_requests.get()
        self.client.force_login(self.b)
        resp = self.click(room, reverse("chat_priority_action", args=[conv.pk, preq.pk, "accept"]))
        self.assertEqual(resp.status_code, 302)
        conv.refresh_from_db()
        self.assertEqual(conv.tier, Conversation.PRIORITY)

    def test_match_connect_button_works(self):
        conv = self.make_chat(Conversation.MATCH, 10)
        self.client.force_login(self.a)
        resp = self.click(reverse("chat_room", args=[conv.pk]), reverse("chat_connect", args=[conv.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ConnectionRequest.objects.filter(conversation=conv, status="pending").exists())

    def test_every_post_form_has_a_token(self):
        match = self.make_chat(Conversation.MATCH, 10)
        post = Post.objects.create(author=self.b, title="t")
        Comment.objects.create(post=post, author=self.b, body="c")
        self.client.force_login(self.a)
        ConnectionRequest.objects.create(from_user=self.b, to_user=self.a, source="match", conversation=match)
        pages = ["/", reverse("post_detail", args=[post.pk]), reverse("new_post"), reverse("my_profile"),
                 reverse("profile", args=[self.b.pk]), reverse("people"), reverse("people") + "?tab=sent",
                 reverse("inbox"), reverse("chat_room", args=[match.pk]), reverse("match")]
        total = 0
        for url in pages:
            total += self.assert_forms_have_tokens(self.client.get(url).content.decode(), url)
        panel = self.client.get(reverse("chat_poll", args=[match.pk])).json()["panel"]
        total += self.assert_forms_have_tokens(panel, "chat poll panel")
        self.assertGreater(total, 20)


class GenderEmojiTests(TestCase):
    MALE = "♂️"

    def setUp(self):
        self.viewer = make_user("Viewer Person", "female", 20, "male")
        self.owner = make_user("Owner Person", "male", 21, "female")
        self.post = Post.objects.create(author=self.owner, title="hello there")
        self.client.force_login(self.viewer)

    def feed(self):
        return self.client.get("/?sort=new").content.decode()

    def test_shown_when_gender_public(self):
        self.assertIn(self.MALE, self.feed())
        self.assertIn(self.MALE, self.client.get(reverse("profile", args=[self.owner.pk])).content.decode())

    def test_hidden_when_gender_private_or_profile_locked(self):
        p = self.owner.profile
        p.set_privacy("gender", ONLY_ME)
        p.save()
        self.assertNotIn(self.MALE, self.feed())
        self.assertNotIn(self.MALE, self.client.get(reverse("profile", args=[self.owner.pk])).content.decode())
        p.set_privacy("gender", PUBLIC)
        p.profile_visible = False
        p.save()
        self.assertNotIn(self.MALE, self.feed())

    def test_not_shown_in_blind_match_chat(self):
        from chat.services import create_match_conversation
        conv = create_match_conversation(self.viewer, self.owner)
        html = self.client.get(reverse("chat_room", args=[conv.pk])).content.decode()
        self.assertNotIn(self.MALE, html)
        self.assertNotIn(self.MALE, self.client.get(reverse("inbox") + "?tab=match").content.decode())


@override_settings(MEDIA_ROOT="/tmp/nitsdate-test-media", MATCH_REVEAL_SECONDS=0)
class BlockAndGalleryTests(TestCase):
    def setUp(self):
        self.a = make_user("Asha Block", "female", 20, "male")
        self.b = make_user("Bikram Block", "male", 21, "female")

    def block(self, who, target, kind, on=True, via_chat=None):
        self.client.force_login(who)
        url = reverse("block_in_chat", args=[via_chat.pk]) if via_chat else reverse("block_user", args=[target.pk])
        return self.client.post(url, {"kind": kind, "on": "1" if on else "0"})

    def upload(self, n, privacy="public"):
        files = [SimpleUploadedFile(f"p{i}.png", png_bytes(), content_type="image/png") for i in range(n)]
        return self.client.post(reverse("gallery_upload"), {"photos": files, "privacy": privacy})

    # ---- profile block ----------------------------------------------------------
    def test_profile_block_hides_everything_from_them_only(self):
        from accounts.models import Block, GalleryPhoto
        self.client.force_login(self.a)
        self.client.post(reverse("edit_field", args=["photo"]),
                         {"value": SimpleUploadedFile("me.png", png_bytes(), content_type="image/png")})
        self.upload(1)
        photo = GalleryPhoto.objects.get(user=self.a)
        Post.objects.create(author=self.a, title="asha post")
        self.block(self.a, self.b, "profile")
        self.assertTrue(Block.objects.get(blocker=self.a, blocked=self.b).profile)

        self.client.force_login(self.b)
        resp = self.client.get(reverse("profile", args=[self.a.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, "isn't available", status_code=404)
        feed = self.client.get("/?sort=new").content.decode()
        self.assertIn(self.a.alias, feed)          # public post still there, under the alias
        self.assertNotIn("Asha Block", feed)
        self.assertNotIn("♀", feed)            # gender hidden too
        self.assertEqual(self.client.get(reverse("profile_photo", args=[self.a.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("gallery_image", args=[self.a.pk, photo.pk])).status_code, 404)
        self.client.post(reverse("send_request", args=[self.a.pk]))
        self.assertFalse(ConnectionRequest.objects.exists())

        # the blocker still sees the other person's profile normally
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(reverse("profile", args=[self.b.pk])).status_code, 200)

        self.block(self.a, self.b, "profile", on=False)
        self.assertFalse(Block.objects.exists())   # row removed when nothing is blocked
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(reverse("profile", args=[self.a.pk])).status_code, 200)

    def test_block_cancels_pending_requests(self):
        ConnectionRequest.objects.create(from_user=self.b, to_user=self.a, source="profile")
        self.block(self.a, self.b, "messages")
        self.assertEqual(ConnectionRequest.objects.get().status, ConnectionRequest.CANCELLED)

    # ---- message block ------------------------------------------------------------
    def test_message_block_pauses_chat_both_ways(self):
        from chat.services import connect_users
        conv = connect_users(self.a, self.b)
        self.block(self.a, self.b, "messages")
        for user in (self.a, self.b):
            self.client.force_login(user)
            resp = self.client.post(reverse("chat_send", args=[conv.pk]), {"text": "hi"}, HTTP_X_REQUESTED_WITH="fetch")
            self.assertEqual(resp.status_code, 400)
            html = self.client.get(reverse("chat_room", args=[conv.pk])).content.decode()
            self.assertNotIn('id="composer"', html)
        self.assertIn("You can't send messages in this chat", html)  # b's view
        self.assertEqual(conv.messages.filter(sender__isnull=False).count(), 0)
        self.block(self.a, self.b, "messages", on=False)
        resp = self.client.post(reverse("chat_send", args=[conv.pk]), {"text": "hi again"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.json(), {"ok": True})

    def test_blocked_people_never_matched(self):
        self.block(self.a, self.b, "messages")
        for user in (self.a, self.b):
            self.client.force_login(user)
            self.client.post(reverse("match_start"), {"min_age": 18, "max_age": 30})
        self.assertFalse(Conversation.objects.exists())

    def test_blocking_from_blind_chat_stays_blind(self):
        from accounts.models import Block
        from chat.services import create_match_conversation
        conv = create_match_conversation(self.a, self.b)
        self.block(self.a, None, "messages", via_chat=conv)
        block = Block.objects.get(blocker=self.a, blocked=self.b)
        self.assertEqual(block.conversation_id, conv.pk)
        for url in (reverse("chat_room", args=[conv.pk]), reverse("people") + "?tab=blocked"):
            html = self.client.get(url).content.decode()
            self.assertNotIn(f"/u/{self.b.pk}/", html)
            self.assertNotIn(self.b.alias, html)
        self.assertIn(conv.nickname_for(self.b), html)

    # ---- gallery ------------------------------------------------------------------
    def test_gallery_limit_is_ten(self):
        from accounts.models import GalleryPhoto
        self.client.force_login(self.a)
        resp = self.upload(12)
        self.assertEqual(GalleryPhoto.objects.filter(user=self.a).count(), 10)
        self.assertContains(self.client.get(reverse("my_profile")), "10 of 10")
        self.upload(1)
        self.assertEqual(GalleryPhoto.objects.filter(user=self.a).count(), 10)

    def test_gallery_privacy_per_photo(self):
        from accounts.models import GalleryPhoto
        self.client.force_login(self.a)
        self.upload(1, "public")
        self.upload(1, "connections")
        self.upload(1, "me")
        pub, con, me_only = GalleryPhoto.objects.filter(user=self.a).order_by("id")

        def visible_to(viewer):
            self.client.force_login(viewer)
            return {p.pk for p in (pub, con, me_only)
                    if self.client.get(reverse("gallery_image", args=[self.a.pk, p.pk])).status_code == 200}

        self.assertEqual(visible_to(self.b), {pub.pk})
        html = self.client.get(reverse("profile", args=[self.a.pk])).content.decode()
        self.assertIn("2 private photos", html)
        Connection.objects.create(user1_id=min(self.a.pk, self.b.pk), user2_id=max(self.a.pk, self.b.pk))
        self.b._connected_ids = None
        self.assertEqual(visible_to(self.b), {pub.pk, con.pk})
        self.assertEqual(visible_to(self.a), {pub.pk, con.pk, me_only.pk})
        # locked profile hides even public photos from strangers
        stranger = make_user("Stranger Gal", "male", 22, "female")
        Profile.objects.filter(user=self.a).update(profile_visible=False)
        self.assertEqual(visible_to(stranger), set())
        # change privacy, make avatar, delete
        self.client.force_login(self.a)
        self.client.post(reverse("gallery_privacy", args=[me_only.pk]), {"privacy": "public"})
        me_only.refresh_from_db()
        self.assertEqual(me_only.privacy, "public")
        self.client.post(reverse("gallery_make_avatar", args=[pub.pk]))
        profile = Profile.objects.get(user=self.a)
        self.assertTrue(profile.photo)
        self.assertEqual(profile.edits_of("photo"), 1)
        self.client.post(reverse("gallery_delete", args=[pub.pk]))
        self.assertFalse(GalleryPhoto.objects.filter(pk=pub.pk).exists())

    def test_new_forms_have_csrf_tokens(self):
        from django.test import Client
        from chat.services import create_match_conversation
        conv = create_match_conversation(self.a, self.b)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.a)
        self.client.force_login(self.a)
        self.upload(1)
        self.block(self.a, None, "messages", via_chat=conv)
        for url in (reverse("my_profile"), reverse("people") + "?tab=blocked", reverse("chat_room", args=[conv.pk]),
                    reverse("profile", args=[self.b.pk])):
            html = client.get(url).content.decode()
            forms = POST_FORM_RE.findall(html)
            self.assertTrue(forms)
            for body in forms:
                self.assertIn("csrfmiddlewaretoken", body, url)



@override_settings(MEDIA_ROOT="/tmp/nitsdate-test-media", MATCH_REVEAL_SECONDS=0)
class AgeGroupTests(TestCase):
    """Under-18s (16-17) are paired only with under-18s; everything else is the same for everyone."""

    def setUp(self):
        self.teen_f = make_user("Teen Girl", "female", 17, "male")
        self.teen_m = make_user("Teen Boy", "male", 16, "female")
        self.adult_m = make_user("Adult Boy", "male", 19, "female")
        self.adult_f = make_user("Adult Girl", "female", 20, "male")

    def search(self, user, lo, hi):
        self.client.force_login(user)
        return self.client.post(reverse("match_start"), {"min_age": lo, "max_age": hi})

    def test_signup_needs_exact_birth_date(self):
        fresh = User.objects.create(username="sixteen", email="sixteen@cse.nits.ac.in")
        self.client.force_login(fresh)
        born = years_ago(16, 3)
        self.client.post(reverse("onboarding"), {"name": "Six Teen", "gender": "female", "birth_date": born,
                                                 "likes": ["male"]})
        p = Profile.objects.get(user=fresh)
        self.assertEqual(p.birth_date.isoformat(), born)
        self.assertEqual(p.age, 16)
        self.assertTrue(p.is_minor)
        # the date can't be edited by the user afterwards
        self.client.post(reverse("edit_field", args=["age"]), {"value": "25"})
        p.refresh_from_db()
        self.assertEqual(p.birth_date.isoformat(), born)
        self.assertContains(self.client.get(reverse("my_profile")), "under-18 group")

    def test_turning_18_moves_group_automatically(self):
        p = self.teen_f.profile
        p.birth_date = timezone.localdate().replace(year=timezone.localdate().year - 18) + timedelta(days=1)
        self.assertTrue(p.is_minor)          # 18th birthday is tomorrow
        p.birth_date -= timedelta(days=1)
        self.assertFalse(p.is_minor)         # birthday today

    def test_matchmaking_never_crosses_groups(self):
        self.search(self.adult_m, 16, 60)    # adult tries to include 16-17: clamped to 18+
        self.assertEqual(MatchSession.objects.get(user=self.adult_m).min_age, 18)
        self.search(self.teen_f, 16, 60)     # teen range clamped to 16-17
        session = MatchSession.objects.get(user=self.teen_f)
        self.assertEqual((session.min_age, session.max_age), (16, 17))
        self.assertFalse(Conversation.objects.exists())  # compatible otherwise, but different groups
        self.search(self.teen_m, 16, 17)
        conv = Conversation.objects.get()
        self.assertEqual({conv.user1_id, conv.user2_id}, {self.teen_f.pk, self.teen_m.pk})
        self.search(self.adult_f, 18, 30)
        self.assertEqual(Conversation.objects.count(), 2)  # the adults matched each other

    def test_connections_never_cross_groups(self):
        self.client.force_login(self.adult_m)
        html = self.client.get(reverse("profile", args=[self.teen_f.pk])).content.decode()
        self.assertIn("only within the same age group", html)
        self.client.post(reverse("send_request", args=[self.teen_f.pk]))
        self.assertFalse(ConnectionRequest.objects.exists())
        self.client.force_login(self.teen_f)
        self.client.post(reverse("send_request", args=[self.adult_m.pk]))
        self.assertFalse(ConnectionRequest.objects.exists())
        self.client.post(reverse("send_request", args=[self.teen_m.pk]))   # same group: fine
        self.assertEqual(ConnectionRequest.objects.count(), 1)
        # a request that became cross-group (e.g. a birthday) can't be accepted
        stale = ConnectionRequest.objects.create(from_user=self.adult_f, to_user=self.teen_m, source="profile")
        self.client.force_login(self.teen_m)
        self.client.post(reverse("request_action", args=[stale.pk, "accept"]))
        self.assertFalse(Connection.objects.filter(user1_id=min(self.adult_f.pk, self.teen_m.pk),
                                                   user2_id=max(self.adult_f.pk, self.teen_m.pk)).exists())

    def test_everything_else_is_the_same_for_under_18s(self):
        from chat.services import connect_users
        self.client.force_login(self.teen_f)
        self.client.post(reverse("new_post"), {"title": "teen post"})
        post = Post.objects.get(title="teen post")
        self.client.force_login(self.adult_m)  # adults see, vote on and comment on it like any post
        self.assertEqual(self.client.post(reverse("vote_post", args=[post.pk]), {"value": 1},
                                          HTTP_X_REQUESTED_WITH="fetch").json()["score"], 1)
        self.client.post(reverse("add_comment", args=[post.pk]), {"body": "nice"})
        self.assertEqual(post.comments.count(), 1)
        conv = connect_users(self.teen_f, self.teen_m)  # teens chat with photos like everyone else
        self.client.force_login(self.teen_f)
        upload = SimpleUploadedFile("pic.png", png_bytes(), content_type="image/png")
        resp = self.client.post(reverse("chat_send", args=[conv.pk]), {"text": "hi", "image": upload},
                                HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.json(), {"ok": True})
        self.client.post(reverse("gallery_upload"),
                         {"photos": [SimpleUploadedFile("g.png", png_bytes(), content_type="image/png")], "privacy": "public"})
        from accounts.models import GalleryPhoto
        self.assertEqual(GalleryPhoto.objects.filter(user=self.teen_f).count(), 1)


class LegalPagesTests(TestCase):
    def test_readable_signed_out_and_before_onboarding(self):
        for url in ("/privacy/", "/terms/"):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(User.objects.create(username="new", email="new@gmail.com"))  # no profile yet
        for url in ("/privacy/", "/terms/"):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertRedirects(self.client.get("/"), reverse("onboarding"))


@override_settings(MEDIA_ROOT="/tmp/nitsdate-test-media")
class DeleteOwnAccountTests(TestCase):
    def setUp(self):
        self.me = make_user("Mira Leaving", "female", 20, "male")
        self.other = make_user("Omar Stays", "male", 21, "female")
        self.client.force_login(self.me)

    def test_page_and_wrong_phrase(self):
        resp = self.client.get(reverse("delete_my_account"))
        self.assertContains(resp, 'type <b>"I want to stay single"</b>', html=False)
        self.client.post(reverse("delete_my_account"), {"phrase": "I want to stay"})
        self.me.refresh_from_db()
        self.assertTrue(self.me.is_active)

    def test_phrase_any_case_deletes_everything(self):
        from chat.services import connect_users
        post = Post.objects.create(author=self.me, title="mine")
        theirs = Post.objects.create(author=self.other, title="theirs", comment_count=1)
        comment = Comment.objects.create(post=theirs, author=self.me, body="my comment")
        conv = connect_users(self.me, self.other)
        Message.objects.create(conversation=conv, sender=self.me, stage=conv.tier, text="hi")

        resp = self.client.post(reverse("delete_my_account"), {"phrase": "  i WANT to stay   SINGLE "})
        self.assertRedirects(resp, reverse("login"))
        self.me.refresh_from_db()
        self.assertFalse(self.me.is_active)
        self.assertEqual(self.me.email, "")
        self.assertFalse(Profile.objects.filter(user=self.me).exists())
        self.assertFalse(Post.objects.filter(pk=post.pk).exists())
        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())
        self.assertFalse(Connection.objects.exists())
        comment.refresh_from_db()
        self.assertTrue(comment.is_deleted)
        self.assertEqual(comment.body, "")
        self.assertTrue(Post.objects.filter(pk=theirs.pk).exists())   # other people's posts stay
        # signed out
        self.assertEqual(self.client.get(reverse("my_profile")).status_code, 302)

    def test_admins_cant_delete_themselves_here(self):
        self.me.is_superuser = True
        self.me.save()
        self.client.post(reverse("delete_my_account"), {"phrase": "I want to stay single"})
        self.me.refresh_from_db()
        self.assertTrue(self.me.is_active)
