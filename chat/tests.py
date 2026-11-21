from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Block, User, pair_ids
from accounts.tests import make_user
from chat.models import Conversation, Message
from chat.presence import presence


class PriorityPresenceTests(TestCase):
    """Priority chats show "Active now / Active x ago" and "Sent / Seen"; other chats don't."""

    def setUp(self):
        self.a = make_user("Asha Rao", gender="female", likes="male")
        self.b = make_user("Bilal Khan", gender="male", likes="female")
        u1, u2 = pair_ids(self.a, self.b)
        self.conv = Conversation.objects.create(user1_id=u1, user2_id=u2, tier=Conversation.PRIORITY)
        self.client.force_login(self.a)

    def seen(self, user, ago):
        User.objects.filter(pk=user.pk).update(last_seen=timezone.now() - ago)

    def test_presence_text(self):
        now = timezone.now()
        self.b.last_seen = None
        self.assertEqual(presence(self.b, now), {"now": False, "text": ""})
        self.b.last_seen = now - timedelta(seconds=70)
        self.assertEqual(presence(self.b, now), {"now": True, "text": "Active now"})
        self.b.last_seen = now - timedelta(minutes=5, seconds=10)
        self.assertEqual(presence(self.b, now)["text"], "Active 5\xa0minutes ago")
        self.b.last_seen = now - timedelta(days=2, hours=3)
        self.assertEqual(presence(self.b, now)["text"], "Active 2\xa0days ago")

    def test_priority_chat_shows_presence_and_the_list_too(self):
        self.seen(self.b, timedelta(seconds=30))
        room = self.client.get(reverse("chat_room", args=[self.conv.pk]))
        self.assertContains(room, 'id="presence"')
        self.assertContains(room, "Active now")
        inbox = self.client.get(reverse("inbox") + "?tab=priority")
        self.assertContains(inbox, "Active now")

    def test_other_chats_show_nothing(self):
        self.seen(self.b, timedelta(seconds=30))
        Conversation.objects.filter(pk=self.conv.pk).update(tier=Conversation.INBOX)
        room = self.client.get(reverse("chat_room", args=[self.conv.pk]))
        self.assertNotContains(room, 'id="presence"')
        self.assertNotContains(room, "Active now")
        self.assertNotIn("presence", self.client.get(reverse("chat_poll", args=[self.conv.pk])).json())

    def test_a_message_block_hides_it(self):
        self.seen(self.b, timedelta(seconds=30))
        Block.objects.create(blocker=self.b, blocked=self.a, messages=True)
        room = self.client.get(reverse("chat_room", args=[self.conv.pk]))
        self.assertNotContains(room, "Active now")

    def test_sent_then_seen(self):
        send = reverse("chat_send", args=[self.conv.pk])
        poll = reverse("chat_poll", args=[self.conv.pk])
        self.client.post(send, {"text": "hi"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(self.client.get(poll + "?after=0").json()["receipt"], "Sent")
        other = self.client_class()
        other.force_login(self.b)
        # loading the page isn't seeing it (it may be a background tab) ...
        page = other.get(reverse("chat_room", args=[self.conv.pk]))
        self.assertEqual(self.client.get(poll + "?after=0").json()["receipt"], "Sent")
        # ... its first poll, which only runs while the tab is visible, is
        other.get(poll + f"?after={page.context['last_id']}")
        self.assertEqual(self.client.get(poll + "?after=0").json()["receipt"], "Seen")
        # once they reply, there's nothing to show under your message
        Message.objects.create(conversation=self.conv, sender=self.b, stage=self.conv.tier, text="hey")
        self.assertEqual(self.client.get(poll + "?after=0").json()["receipt"], "")

    def test_poll_never_marks_past_the_newest_message(self):
        msg = Message.objects.create(conversation=self.conv, sender=self.b, stage=self.conv.tier, text="hey")
        self.client.get(reverse("chat_poll", args=[self.conv.pk]) + "?after=999999999")
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.last_read_id(self.a), msg.pk)

    def test_read_position_never_moves_back(self):
        first = Message.objects.create(conversation=self.conv, sender=self.b, stage=self.conv.tier, text="1")
        second = Message.objects.create(conversation=self.conv, sender=self.b, stage=self.conv.tier, text="2")
        stale = Conversation.objects.get(pk=self.conv.pk)   # loaded before the newer read lands
        Conversation.objects.get(pk=self.conv.pk).mark_read(self.a, second.pk)
        stale.mark_read(self.a, first.pk)
        self.assertEqual(Conversation.objects.get(pk=self.conv.pk).last_read_id(self.a), second.pk)

    def test_other_chats_still_mark_read_on_open(self):
        Conversation.objects.filter(pk=self.conv.pk).update(tier=Conversation.INBOX)
        msg = Message.objects.create(conversation=self.conv, sender=self.b, stage=Conversation.INBOX, text="yo")
        self.client.get(reverse("chat_room", args=[self.conv.pk]))
        self.assertEqual(Conversation.objects.get(pk=self.conv.pk).last_read_id(self.a), msg.pk)
