import json
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.auth import AuthMiddlewareStack
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import ConnectionRequest, User, pair_ids
from accounts.tests import make_user
from chat.models import Conversation
from live.matcher import Matcher
from live.routing import websocket_urlpatterns
from matchmaking.models import MatchSession
from matchmaking.services import PENDING
from moderation.models import ModeratorGrant, Report
from moderation.services import delete_account
from notifications.models import Notification

SITE = (b"origin", b"http://testserver")


class Socket(ApplicationCommunicator):
    """A page's WebSocket, talking to the app directly (channels.testing would need daphne)."""

    def __init__(self, app, path, headers):
        super().__init__(app, {"type": "websocket", "path": path, "query_string": b"",
                               "headers": headers, "subprotocols": []})

    async def connect(self, timeout=3):
        await self.send_input({"type": "websocket.connect"})
        out = await self.receive_output(timeout)
        return out["type"] == "websocket.accept", out.get("code")

    async def send_json_to(self, data):
        await self.send_input({"type": "websocket.receive", "text": json.dumps(data)})

    async def receive_json_from(self, timeout=3):
        out = await self.receive_output(timeout)
        assert out["type"] == "websocket.send", out
        return json.loads(out["text"])

    async def disconnect(self, code=1000, timeout=3):
        await self.send_input({"type": "websocket.disconnect", "code": code})
        await self.wait(timeout)


def socket_app():
    # built per test so it sees the test run's ALLOWED_HOSTS
    return AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)))


async def open_socket(client=None, origin=SITE):
    headers = [origin] if origin else []
    cookie = client.cookies.get(settings.SESSION_COOKIE_NAME) if client else None
    if cookie:
        headers.append((b"cookie", f"{settings.SESSION_COOKIE_NAME}={cookie.value}".encode()))
    socket = Socket(socket_app(), "/ws/live/", headers)
    connected, _ = await socket.connect()
    return socket, connected


async def next_of(socket, kind, timeout=3):
    """The next message of this kind, skipping others."""
    while True:
        data = await socket.receive_json_from(timeout=timeout)
        if data.get("t") == kind:
            return data


async def assert_refused(test, socket, connected):
    test.assertTrue(connected)   # accepted only to say why, so the page stops retrying
    test.assertEqual(await socket.receive_output(timeout=3), {"type": "websocket.close", "code": 4401})


def signed_in(user):
    client = Client()
    client.force_login(user)
    return client


@override_settings(MATCH_REVEAL_SECONDS=3)
class LiveSocketTests(TransactionTestCase):
    def setUp(self):
        async_to_sync(get_channel_layer().flush)()
        self.a = make_user("Asha Rao", gender="female", likes="male")
        self.b = make_user("Bilal Khan", gender="male", likes="female")
        self.ca, self.cb = signed_in(self.a), signed_in(self.b)

    def conversation(self):
        u1, u2 = pair_ids(self.a, self.b)
        return Conversation.objects.create(user1_id=u1, user2_id=u2, tier=Conversation.INBOX)

    # ---- who may connect ------------------------------------------------------------------

    async def test_refuses_signed_out_visitors(self):
        socket, connected = await open_socket()
        await assert_refused(self, socket, connected)

    async def test_refuses_accounts_without_a_profile(self):
        bare = await sync_to_async(User.objects.create)(username="bare", email="bare@cse.nits.ac.in")
        socket, connected = await open_socket(await sync_to_async(signed_in)(bare))
        await assert_refused(self, socket, connected)

    async def test_refuses_emails_no_longer_allowed(self):
        outsider = await sync_to_async(make_user)("Out Sider")
        await sync_to_async(User.objects.filter(pk=outsider.pk).update)(email="out.sider@gmail.com")
        client = await sync_to_async(signed_in)(outsider)
        socket, connected = await open_socket(client)
        await assert_refused(self, socket, connected)

    async def test_refuses_pages_on_other_sites(self):
        socket, connected = await open_socket(self.ca, origin=(b"origin", b"https://evil.example"))
        self.assertFalse(connected)

    # ---- what it sends ----------------------------------------------------------------------

    async def test_sends_badges_on_connect_and_answers_pings(self):
        socket, connected = await open_socket(self.ca)
        self.assertTrue(connected)
        badges = await next_of(socket, "badges")
        self.assertEqual((badges["messages"], badges["requests"], badges["notifications"]), (0, 0, 0))
        self.assertNotIn("reports", badges)
        await socket.send_json_to({"t": "ping", "visible": True})
        await next_of(socket, "pong")
        await socket.disconnect()
        self.assertIsNotNone(await sync_to_async(lambda: User.objects.get(pk=self.a.pk).last_seen)())

    async def test_hidden_tabs_dont_count_as_online(self):
        socket, _ = await open_socket(self.ca)
        await socket.send_json_to({"t": "ping", "visible": False})
        await next_of(socket, "pong")
        await socket.disconnect()
        self.assertIsNone(await sync_to_async(lambda: User.objects.get(pk=self.a.pk).last_seen)())

    async def test_a_new_message_reaches_the_other_person_at_once(self):
        conv = await sync_to_async(self.conversation)()
        sa, _ = await open_socket(self.ca)
        sb, _ = await open_socket(self.cb)
        await next_of(sa, "badges")
        await next_of(sb, "badges")

        res = await sync_to_async(self.ca.post)(reverse("chat_send", args=[conv.pk]), {"text": "hello"},
                                                HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(await next_of(sb, "chat"), {"t": "chat", "conv": conv.pk})
        self.assertEqual((await next_of(sb, "badges"))["messages"], 1)
        self.assertEqual(await next_of(sa, "chat"), {"t": "chat", "conv": conv.pk})   # sender's other tabs

        # reading it (the open chat fetches what's new) clears the badge in their other tabs
        await sync_to_async(self.cb.get)(reverse("chat_poll", args=[conv.pk]) + "?after=0")
        self.assertEqual((await next_of(sb, "badges"))["messages"], 0)
        await sa.disconnect()
        await sb.disconnect()

    async def test_requests_and_notifications_update_the_badges(self):
        sb, _ = await open_socket(self.cb)
        await next_of(sb, "badges")
        await sync_to_async(ConnectionRequest.objects.create)(
            from_user=self.a, to_user=self.b, source=ConnectionRequest.SOURCE_PROFILE)
        self.assertEqual((await next_of(sb, "badges"))["requests"], 1)

        from feed.models import Post
        from notifications.services import notify
        post = await sync_to_async(Post.objects.create)(author=self.b, title="Hi", body="hi")
        await sync_to_async(notify)(self.b, self.a, Notification.POST_UPVOTE, post)
        self.assertEqual((await next_of(sb, "badges"))["notifications"], 1)
        await sb.disconnect()

    async def test_new_reports_reach_moderators_only(self):
        await sync_to_async(ModeratorGrant.objects.create)(email=self.b.email)
        sa, _ = await open_socket(self.ca)
        sb, _ = await open_socket(self.cb)
        await next_of(sa, "badges")
        self.assertEqual((await next_of(sb, "badges"))["reports"], 0)
        await sync_to_async(Report.objects.create)(kind=Report.PROFILE, reason="spam", reporter=self.a,
                                                   reported_user=self.b)
        self.assertEqual((await next_of(sb, "badges"))["reports"], 1)
        self.assertTrue(await sa.receive_nothing(timeout=1))   # nothing for a normal user
        await sa.disconnect()
        await sb.disconnect()

    async def test_matcher_pairs_searches_and_tells_both_people(self):
        def search(user):
            s = MatchSession.objects.create(user=user, min_age=18, max_age=30,
                                            expires_at=timezone.now() + timedelta(hours=1))
            MatchSession.objects.filter(pk=s.pk).update(created_at=timezone.now() - timedelta(seconds=10))
            return s
        mine = await sync_to_async(search)(self.a)
        await sync_to_async(search)(self.b)
        sa, _ = await open_socket(self.ca)
        sb, _ = await open_socket(self.cb)

        self.assertEqual(await sync_to_async(Matcher().run_once)(), 1)
        url_a = (await next_of(sa, "match"))["url"]
        url_b = (await next_of(sb, "match"))["url"]
        conv_id = await sync_to_async(lambda: MatchSession.objects.get(pk=mine.pk).conversation_id)()
        self.assertEqual(url_a, reverse("chat_room", args=[conv_id]))
        self.assertEqual(url_b, url_a)
        await sa.disconnect()
        await sb.disconnect()

        # the browser that showed "matching" lands in the chat: the search is settled there, so
        # /match/ won't bounce back into the chat later
        def land():
            session = self.ca.session
            session[PENDING] = mine.pk
            session.save()
            page = self.ca.get(url_a)
            return page, PENDING in self.ca.session
        page, still_pending = await sync_to_async(land)()
        self.assertContains(page, "It&#x27;s a match!")
        self.assertFalse(still_pending)

    async def test_new_searches_wait_for_the_matching_screen(self):
        def fresh(user):
            return MatchSession.objects.create(user=user, min_age=18, max_age=30,
                                               expires_at=timezone.now() + timedelta(hours=1))
        await sync_to_async(fresh)(self.a)
        await sync_to_async(fresh)(self.b)
        self.assertEqual(await sync_to_async(Matcher().run_once)(), 0)   # under MATCH_REVEAL_SECONDS old

    async def test_deleting_an_account_disconnects_it(self):
        sb, _ = await open_socket(self.cb)
        await next_of(sb, "badges")
        await sync_to_async(delete_account)(self.b)
        while True:
            out = await sb.receive_output(timeout=3)
            if out["type"] == "websocket.close":
                break
        self.assertEqual(out["code"], 4401)


class PublishFailureTests(TestCase):
    def test_a_broken_redis_never_breaks_the_page(self):
        a = make_user("Asha Rao", gender="female", likes="male")
        b = make_user("Bilal Khan", gender="male", likes="female")
        u1, u2 = pair_ids(a, b)
        conv = Conversation.objects.create(user1_id=u1, user2_id=u2, tier=Conversation.INBOX)
        broken = mock.Mock()
        broken.group_send = mock.AsyncMock(side_effect=ConnectionError("redis is down"))
        with mock.patch("live.publish.get_channel_layer", return_value=broken), \
                self.assertLogs("live.publish", "WARNING"), \
                self.captureOnCommitCallbacks(execute=True):
            res = signed_in(a).post(reverse("chat_send", args=[conv.pk]), {"text": "hello"},
                                    HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(res.json(), {"ok": True})
        self.assertTrue(broken.group_send.called)
