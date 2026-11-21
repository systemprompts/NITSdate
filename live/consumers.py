"""One WebSocket per open tab, at /ws/live/.

Server → page:  {"t": "badges", ...counts}   the nav badge numbers (same as chat.views.badges)
                {"t": "chat", "conv": id}     that chat changed; an open chat page fetches what's new
                {"t": "match", "url": ...}    a matchmaking search found someone
                {"t": "pong"}
                {"t": "typing", "conv": id}   the other person is typing in your Priority chat
Page → server:  {"t": "ping", "visible": bool} every 30 s; keeps Cloudflare from closing an idle
                socket and, while the tab is visible, keeps "last online" current.
                {"t": "typing", "conv": id} while typing in a Priority chat (at most every few seconds)
Close code 4401 means "you may not use the site": the page stops reconnecting.
"""
import asyncio
import json
import time
import weakref
from datetime import timedelta

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .publish import MODS, user_group

NOT_ALLOWED = 4401
BADGE_DELAY = 0.25          # one recount for a burst of signals (connecting sends several)
SEEN_EVERY = 60             # seconds between last_seen writes, like accounts.middleware.LastSeenMiddleware
REJOIN_EVERY = 30 * 60      # channels_redis forgets group members after a day
TYPING_EVERY = 2            # forward at most one "typing" per chat this often
PEER_FOR = 60               # how long a typing check (Priority chat, no block) is trusted


def db(func):
    # these functions touch nothing shared, so they can run side by side in the thread pool
    return database_sync_to_async(func, thread_sensitive=False)


def _allowed_user(user_id):
    """The account as it is now, or None if it may no longer use the site. Same rules as
    accounts.middleware.OnboardingMiddleware: active, college or allowlisted email, has a profile."""
    from accounts.adapters import email_allowed, may_sign_in
    from accounts.models import User
    from moderation.permissions import is_mod

    user = User.objects.select_related("profile").filter(pk=user_id, is_active=True).first()
    if user is None or not user.has_profile:
        return None
    if not (email_allowed(user.email) or user.is_superuser or may_sign_in(user.email)):
        return None
    user.live_is_mod = is_mod(user)
    return user


def _badge_data(user_id):
    from accounts.context_processors import badge_counts
    from chat.services import inbox_version

    user = _allowed_user(user_id)
    if user is None:
        return None
    data = badge_counts(user)
    data["inbox"] = inbox_version(user, data["messages"])
    return data


def _note_seen(user_id):
    from accounts.models import User

    now = timezone.now()
    (User.objects.filter(pk=user_id, is_active=True)
     .filter(Q(last_seen__isnull=True) | Q(last_seen__lt=now - timedelta(seconds=SEEN_EVERY)))
     .update(last_seen=now))


def _typing_peer(user_id, conv_id):
    """Who should see `user_id` typing in this chat: the other person, if it's their active Priority
    chat with no message block and messaging isn't turned off for them. Otherwise None."""
    from accounts.privacy import messages_blocked
    from chat.models import Conversation
    from moderation.models import Restriction

    conv = (Conversation.objects.filter(Q(user1_id=user_id) | Q(user2_id=user_id), pk=conv_id,
                                        tier=Conversation.PRIORITY, is_active=True)
            .only("id", "user1_id", "user2_id").first())
    if conv is None or messages_blocked(conv.user1_id, conv.user2_id):
        return None
    if Restriction.objects.filter(user_id=user_id, no_messaging=True).exists():
        return None
    return conv.user2_id if conv.user1_id == user_id else conv.user1_id


allowed_user, badge_data, note_seen = db(_allowed_user), db(_badge_data), db(_note_seen)
typing_peer = db(_typing_peer)


# Several tabs of one person get the same signal at the same moment; the first one counts and the
# others reuse that result. Kept per event loop (one in production; tests start a new one each).
_shared = weakref.WeakKeyDictionary()


def _shared_state():
    loop = asyncio.get_running_loop()
    state = _shared.get(loop)
    if state is None:
        state = _shared[loop] = {"counts": {}, "locks": {}}
    return state


class LiveConsumer(AsyncJsonWebsocketConsumer):
    user_id = None
    joined = ()

    async def connect(self):
        user = self.scope.get("user")
        current = await allowed_user(user.pk) if getattr(user, "is_authenticated", False) else None
        await self.accept()   # accept first so the page sees the close code and stops retrying
        if current is None:
            await self.close(code=NOT_ALLOWED)
            return
        self.user_id = current.pk
        self.joined = [user_group(current.pk)] + ([MODS] if current.live_is_mod else [])
        await self._join()
        self.badges_wanted = time.monotonic()
        self.badge_task = None
        self.seen_at = 0.0
        await self.send_badges(self.badges_wanted)

    async def disconnect(self, code):
        for group in self.joined:
            await self.channel_layer.group_discard(group, self.channel_name)
        task = getattr(self, "badge_task", None)
        if task:
            task.cancel()

    async def _join(self):
        for group in self.joined:
            await self.channel_layer.group_add(group, self.channel_name)
        self.joined_at = time.monotonic()

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        # anything that isn't a small JSON object is ignored rather than raising
        try:
            content = json.loads(text_data) if text_data else None
        except ValueError:
            content = None
        if isinstance(content, dict):
            await self.receive_json(content, **kwargs)

    async def receive_json(self, content, **kwargs):
        if self.user_id is None:
            return
        if content.get("t") == "typing":
            await self._typing(content.get("conv"))
            return
        if content.get("t") != "ping":
            return
        await self.send_json({"t": "pong"})
        now = time.monotonic()
        if content.get("visible") and now - self.seen_at >= SEEN_EVERY:
            self.seen_at = now
            await note_seen(self.user_id)
        if now - self.joined_at >= REJOIN_EVERY:
            await self._join()

    async def _typing(self, conv_id):
        if not isinstance(conv_id, int) or isinstance(conv_id, bool):
            return
        now = time.monotonic()
        if not hasattr(self, "typing_sent"):
            self.typing_sent, self.peers = {}, {}
        sent = self.typing_sent
        if now - sent.get(conv_id, 0) < TYPING_EVERY:
            return
        peer, checked = self.peers.get(conv_id, (None, 0))
        if now - checked >= PEER_FOR:
            peer = await typing_peer(self.user_id, conv_id)
            self.peers[conv_id] = (peer, now)
        if peer:
            sent[conv_id] = now
            await self.channel_layer.group_send(user_group(peer), {"type": "live.typing", "conv": conv_id})

    # ---- signals from live.publish -------------------------------------------------------

    async def live_typing(self, event):
        await self.send_json({"t": "typing", "conv": event["conv"]})

    async def live_badges(self, event):
        self._badges_soon()

    async def live_chat(self, event):
        await self.send_json({"t": "chat", "conv": event["conv"]})
        self._badges_soon()

    async def live_match(self, event):
        await self.send_json({"t": "match", "url": reverse("chat_room", args=[event["conv"]])})
        self._badges_soon()

    async def live_close(self, event):
        if await allowed_user(self.user_id) is None:
            await self.close(code=NOT_ALLOWED)

    # ---- badge counts ---------------------------------------------------------------------

    def _badges_soon(self):
        self.badges_wanted = time.monotonic()
        if self.badge_task is None or self.badge_task.done():
            self.badge_task = asyncio.ensure_future(self._send_when_quiet())

    async def _send_when_quiet(self):
        while True:
            await asyncio.sleep(BADGE_DELAY)
            asked = self.badges_wanted
            await self.send_badges(asked)
            if self.badges_wanted == asked:   # nothing new arrived while counting
                return

    async def send_badges(self, asked):
        """Send counts that are no older than `asked` (when the latest signal arrived)."""
        state = _shared_state()
        lock = state["locks"].setdefault(self.user_id, asyncio.Lock())
        async with lock:
            hit = state["counts"].get(self.user_id)
            if hit and hit[0] >= asked:
                data = hit[1]
            else:
                started = time.monotonic()
                data = await badge_data(self.user_id)
                state["counts"][self.user_id] = (started, data)
                if len(state["counts"]) > 2000:   # forget people who haven't had news for a while
                    old = started - 60
                    for uid in [u for u, (t, _) in state["counts"].items() if t < old]:
                        state["counts"].pop(uid, None)
                        if not state["locks"].get(uid, lock).locked():
                            state["locks"].pop(uid, None)
        if data is None:
            await self.close(code=NOT_ALLOWED)
            return
        await self.send_json({"t": "badges", **data})
