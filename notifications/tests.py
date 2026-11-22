from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Block, ConnectionRequest
from accounts.tests import make_user
from chat.services import create_match_conversation
from feed.models import Comment, Post

from .models import Notification
from .services import unread_count


class NotificationTests(TestCase):
    def setUp(self):
        self.author = make_user("Asha Author", "female", 20, "male")
        self.bob = make_user("Bob Reader", "male", 21, "female")
        self.cara = make_user("Cara Reader", "female", 22, "male")
        self.post = Post.objects.create(author=self.author, title="My first post")
        self.clients = {}

    def as_(self, user):
        if user.pk not in self.clients:
            c = Client()
            c.force_login(user)
            self.clients[user.pk] = c
        return self.clients[user.pk]

    def vote(self, user, value=1, comment=None):
        url = reverse("vote_comment", args=[comment.pk]) if comment else reverse("vote_post", args=[self.post.pk])
        return self.as_(user).post(url, {"value": value})

    def comment(self, user, body="nice", parent=None):
        data = {"body": body, **({"parent": parent.pk} if parent else {})}
        self.as_(user).post(reverse("add_comment", args=[self.post.pk]), data)
        return Comment.objects.filter(author=user).latest("id")

    def test_upvotes_are_grouped_and_anonymous(self):
        self.vote(self.bob)
        self.vote(self.cara)
        self.vote(self.author)                                    # your own vote: nothing
        self.assertEqual(Notification.objects.filter(recipient=self.author).count(), 2)
        self.assertEqual(unread_count(self.author), 1)            # one line: 2 people upvoted your post
        page = self.as_(self.author).get(reverse("notifications")).content.decode()
        self.assertIn("2 people", page)
        self.assertIn("upvoted your post", page)
        for name in ("Bob Reader", "Cara Reader", self.bob.alias, self.cara.alias):
            self.assertNotIn(name, page)                          # never who voted
        self.assertEqual(unread_count(self.author), 0)            # opening the page reads them

    def test_toggling_a_vote_never_piles_up(self):
        for _ in range(3):
            self.vote(self.bob)                                   # up
            self.vote(self.bob)                                   # same arrow again: vote removed
        self.assertFalse(Notification.objects.exists())           # taken back before it was seen
        self.vote(self.bob)
        self.vote(self.bob, -1)                                   # switched to a downvote
        self.vote(self.bob)
        self.assertEqual(Notification.objects.count(), 1)

    def test_comments_and_replies(self):
        c1 = self.comment(self.bob, "first!")
        n = Notification.objects.get(recipient=self.author)
        self.assertEqual((n.kind, n.comment_id), (Notification.COMMENT, c1.pk))
        self.comment(self.cara, "agreed", parent=c1)              # reply to bob, on the author's post
        kinds = sorted(Notification.objects.filter(comment__author=self.cara).values_list("recipient_id", "kind"))
        self.assertEqual(kinds, sorted([(self.bob.pk, Notification.REPLY), (self.author.pk, Notification.COMMENT)]))
        mine = self.comment(self.author, "thanks")                 # own post: nobody to tell
        self.comment(self.bob, "you're welcome", parent=mine)     # reply to the author: one notification, not two
        self.assertEqual(Notification.objects.filter(comment__author=self.bob, comment__parent=mine).count(), 1)
        page = self.as_(self.bob).get(reverse("notifications")).content.decode()
        self.assertIn("replied to your comment", page)
        self.assertIn("agreed", page)

    def test_comment_upvote(self):
        c1 = self.comment(self.bob)
        self.vote(self.cara, comment=c1)
        self.assertTrue(Notification.objects.filter(recipient=self.bob, kind=Notification.COMMENT_UPVOTE).exists())

    def test_settings_switch_kinds_off(self):
        self.as_(self.author).post(reverse("notification_settings"), {"notify_comments": "on"})   # upvotes off
        self.author.refresh_from_db()
        self.assertEqual((self.author.notify_upvotes, self.author.notify_comments, self.author.notify_replies),
                         (False, True, False))
        self.vote(self.bob)
        self.comment(self.bob)
        self.assertEqual(list(Notification.objects.values_list("kind", flat=True)), [Notification.COMMENT])

    def test_blocked_people_cant_notify_you(self):
        Block.objects.create(blocker=self.author, blocked=self.bob, messages=True)
        self.vote(self.bob)
        self.comment(self.bob)
        self.assertFalse(Notification.objects.exists())

    def test_badge_and_people_counts(self):
        self.comment(self.bob)
        badges = self.as_(self.author).get(reverse("nav_badges")).json()
        self.assertEqual(badges["notifications"], 1)
        # a connect request from a matchmaking chat counts on People, like one from a profile
        conv = create_match_conversation(self.author, self.cara)
        ConnectionRequest.objects.create(from_user=self.cara, to_user=self.author,
                                         source=ConnectionRequest.SOURCE_MATCH, conversation=conv)
        ConnectionRequest.objects.create(from_user=self.bob, to_user=self.author)
        badges = self.as_(self.author).get(reverse("nav_badges")).json()
        self.assertEqual(badges["requests"], 2)
        self.assertContains(self.as_(self.author).get(reverse("home")), 'data-badge="notifications"')

    def test_deleting_an_account_clears_its_notifications(self):
        from moderation.services import delete_account
        self.comment(self.bob)
        self.vote(self.bob)
        delete_account(self.bob, remove_content=True)
        self.assertFalse(Notification.objects.exists())


# ---- push notifications -------------------------------------------------------------------------
import json  # noqa: E402
from unittest import mock  # noqa: E402

from django.test import override_settings  # noqa: E402

from accounts.models import pair_ids  # noqa: E402
from chat.models import Conversation, Message  # noqa: E402

from . import push  # noqa: E402
from .models import PushSubscription  # noqa: E402
from .vapid import new_keys  # noqa: E402

KEYS = dict(line.split("=", 1) for line in new_keys().splitlines())
FCM = "https://fcm.googleapis.com/fcm/send/abc123"


@override_settings(**KEYS)
class PushTests(TestCase):
    """New messages and matches reach phones that said yes, saying who wrote but never what."""

    def setUp(self):
        self.a = make_user("Asha Rao", "female", 20, "male")
        self.b = make_user("Bilal Khan", "male", 21, "female")
        self.client.force_login(self.b)
        self.sent = []
        patches = [mock.patch.object(push, "_later", side_effect=lambda fn, *args: fn(*args)),
                   mock.patch.object(push, "_deliver", side_effect=lambda sub, data: self.sent.append(
                       (sub.user_id, json.loads(data))))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def subscribe(self, client=None, endpoint=FCM):
        body = {"endpoint": endpoint, "keys": {"p256dh": "BPk" + "x" * 84, "auth": "y" * 22}}
        return (client or self.client).post(reverse("push_subscribe"), json.dumps(body), content_type="application/json")

    def chat(self, tier=Conversation.INBOX):
        u1, u2 = pair_ids(self.a, self.b)
        return Conversation.objects.create(user1_id=u1, user2_id=u2, tier=tier,
                                           origin=Conversation.ORIGIN_MATCH if tier == Conversation.MATCH
                                           else Conversation.ORIGIN_CONNECTION)

    def write(self, conv, text="hi"):
        sender = Client()
        sender.force_login(self.a)
        return sender.post(reverse("chat_send", args=[conv.pk]), {"text": text}, HTTP_X_REQUESTED_WITH="fetch")

    def test_subscribe_checks_where_it_would_send(self):
        self.assertEqual(self.subscribe().status_code, 200)
        sub = PushSubscription.objects.get()
        self.assertEqual((sub.user, sub.endpoint), (self.b, FCM))
        self.assertTrue(sub.session_key)
        for bad in ("https://evil.example/push", "http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.example/x"):
            self.assertEqual(self.subscribe(endpoint=bad).status_code, 400, bad)
        self.assertTrue(push.endpoint_allowed("https://updates.push.services.mozilla.com/wpush/v2/x"))
        self.assertTrue(push.endpoint_allowed("https://web.push.apple.com/abc"))
        self.assertTrue(push.endpoint_allowed("https://wns2-par02p.notify.windows.com/w/?token=x"))
        # the same browser signing in as someone else moves to them
        other = Client()
        other.force_login(self.a)
        self.subscribe(client=other)
        self.assertEqual(PushSubscription.objects.get().user, self.a)
        with self.settings(VAPID_PRIVATE_KEY=""):
            self.assertEqual(self.subscribe().status_code, 404)

    def test_a_message_tells_who_wrote_never_what(self):
        self.subscribe()
        conv = self.chat()
        self.write(conv, "my secret plan")
        self.assertEqual(self.sent, [(self.b.pk, {"title": "Asha Rao", "body": "Sent you a message",
                                                  "url": f"/messages/{conv.pk}/", "tag": f"chat-{conv.pk}",
                                                  "quiet_if": "page"})])

    def test_one_notification_while_a_burst_is_unread(self):
        self.subscribe()
        conv = self.chat()
        self.write(conv, "one")
        self.write(conv, "two")
        self.assertEqual(len(self.sent), 1)
        conv.refresh_from_db()
        conv.mark_read(self.b, Message.objects.order_by("-id").first().pk)   # they read it
        self.write(conv, "three")
        self.assertEqual(len(self.sent), 2)

    def test_matchmaking_chats_stay_blind(self):
        self.subscribe()
        conv = self.chat(Conversation.MATCH)
        self.write(conv)
        self.assertEqual(self.sent[0][1]["title"], conv.nickname_for(self.a))
        self.assertNotIn("Asha", json.dumps(self.sent))

    def test_a_match_tells_both(self):
        self.subscribe()
        push.match_found(self.b.pk, 7)
        self.assertEqual(self.sent[0][1]["url"], "/messages/7/")
        self.assertEqual(self.sent[0][1]["quiet_if"], "site")
        self.assertIn("match", self.sent[0][1]["title"])

    def test_no_subscription_no_push(self):
        self.write(self.chat())
        self.assertEqual(self.sent, [])

    def test_signing_out_stops_this_browser_only(self):
        self.subscribe()
        phone = Client()
        phone.force_login(self.b)
        self.subscribe(client=phone, endpoint=FCM + "-phone")
        self.client.post(reverse("logout"))
        self.assertEqual(list(PushSubscription.objects.values_list("endpoint", flat=True)), [FCM + "-phone"])

    def test_deleting_the_account_removes_them(self):
        from moderation.services import delete_account
        self.subscribe()
        delete_account(self.b)
        self.assertFalse(PushSubscription.objects.exists())

    def test_pages_offer_it_and_the_worker_is_public(self):
        page = self.client.get(reverse("notifications")).content.decode()
        self.assertIn(f'<meta name="push-key" content="{KEYS["VAPID_PUBLIC_KEY"]}"', page)
        self.assertIn('id="push-ask"', page)
        self.assertIn("data-push-panel", page)
        with self.settings(VAPID_PRIVATE_KEY=""):
            self.assertNotIn('name="push-key"', self.client.get(reverse("notifications")).content.decode())
        anon = Client()
        sw = anon.get("/sw.js")
        self.assertEqual((sw.status_code, sw["Content-Type"]), (200, "application/javascript"))
        self.assertIn("no-store", sw["Cache-Control"])
        self.assertIn("showNotification", sw.content.decode())
        manifest = json.loads(anon.get("/manifest.webmanifest").content)
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(len(manifest["icons"]), 3)


@override_settings(**KEYS)
class PushDeliveryTests(TestCase):
    def setUp(self):
        self.user = make_user("Asha Rao", "female", 20, "male")
        self.sub = PushSubscription.objects.create(user=self.user, endpoint=FCM, key=push.endpoint_key(FCM),
                                                   p256dh="p", auth="a")

    def test_delivered(self):
        with mock.patch("pywebpush.webpush") as webpush:
            self.assertTrue(push._deliver(self.sub, '{"title": "x"}'))
        kwargs = webpush.call_args.kwargs
        self.assertEqual(kwargs["subscription_info"]["endpoint"], FCM)
        self.assertEqual(kwargs["vapid_claims"], {"sub": "https://nitsdate.site"})
        self.assertEqual(kwargs["vapid_private_key"], KEYS["VAPID_PRIVATE_KEY"])
        self.assertEqual(kwargs["ttl"], push.TTL)
        self.sub.refresh_from_db()
        self.assertIsNotNone(self.sub.last_sent_at)

    def test_a_gone_subscription_is_forgotten(self):
        from pywebpush import WebPushException
        gone = WebPushException("gone", response=mock.Mock(status_code=410))
        with mock.patch("pywebpush.webpush", side_effect=gone):
            self.assertFalse(push._deliver(self.sub, "{}"))
        self.assertFalse(PushSubscription.objects.exists())

    def test_other_failures_keep_it(self):
        from pywebpush import WebPushException
        with mock.patch("pywebpush.webpush", side_effect=WebPushException("busy", response=mock.Mock(status_code=503))):
            self.assertFalse(push._deliver(self.sub, "{}"))
        self.assertTrue(PushSubscription.objects.exists())

    def test_keys_look_right(self):
        public, private = KEYS["VAPID_PUBLIC_KEY"], KEYS["VAPID_PRIVATE_KEY"]
        self.assertEqual((len(public), len(private)), (87, 43))     # 65-byte point, 32-byte secret, base64url
