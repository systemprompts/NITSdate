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


class MessageActionTests(TestCase):
    """Reply, edit and delete. Nothing is erased: the chat shows "Edited" / "Message deleted", and
    moderators see the earlier text on reports."""

    def setUp(self):
        self.a = make_user("Asha Rao", gender="female", likes="male")
        self.b = make_user("Bilal Khan", gender="male", likes="female")
        u1, u2 = pair_ids(self.a, self.b)
        self.conv = Conversation.objects.create(user1_id=u1, user2_id=u2, tier=Conversation.INBOX,
                                                origin=Conversation.ORIGIN_CONNECTION)
        self.client.force_login(self.a)
        self.other = self.client_class()
        self.other.force_login(self.b)

    def say(self, user, text, **extra):
        return Message.objects.create(conversation=self.conv, sender=user, stage=self.conv.tier, text=text, **extra)

    def room(self, client):
        return client.get(reverse("chat_room", args=[self.conv.pk])).content.decode()

    def edit(self, msg, text, client=None):
        return (client or self.client).post(reverse("chat_msg_edit", args=[msg.pk]), {"text": text},
                                            HTTP_X_REQUESTED_WITH="fetch")

    def delete(self, msg, client=None):
        return (client or self.client).post(reverse("chat_msg_delete", args=[msg.pk]), HTTP_X_REQUESTED_WITH="fetch")

    def test_reply(self):
        hers = self.say(self.b, "want to get chai?")
        self.client.post(reverse("chat_send", args=[self.conv.pk]), {"text": "yes!", "reply_to": hers.pk},
                         HTTP_X_REQUESTED_WITH="fetch")
        reply = Message.objects.get(text="yes!")
        self.assertEqual(reply.reply_to, hers)
        page = self.room(self.other)
        self.assertIn("Asha Rao replied to you", page)
        self.assertIn(f'data-jump="{hers.pk}"', page)
        self.assertIn("You replied to Bilal Khan", self.room(self.client))
        # a message from another chat can't be quoted
        elsewhere = Conversation.objects.create(user1_id=self.a.pk, user2_id=make_user("Chitra Nair").pk,
                                                tier=Conversation.INBOX, origin=Conversation.ORIGIN_CONNECTION)
        foreign = Message.objects.create(conversation=elsewhere, sender=self.a, stage=elsewhere.tier, text="secret")
        self.client.post(reverse("chat_send", args=[self.conv.pk]), {"text": "x", "reply_to": foreign.pk},
                         HTTP_X_REQUESTED_WITH="fetch")
        self.assertIsNone(Message.objects.get(text="x").reply_to)

    def test_edit_shows_edited_but_keeps_the_old_text_for_moderators(self):
        mine = self.say(self.a, "see you at 5")
        self.assertEqual(self.edit(mine, "see you at 6").status_code, 200)
        mine.refresh_from_db()
        self.assertEqual(mine.text, "see you at 6")
        self.assertIsNotNone(mine.edited_at)
        self.assertEqual(list(mine.edits.values_list("text", flat=True)), ["see you at 5"])
        page = self.room(self.other)
        self.assertIn("see you at 6", page)
        self.assertIn('<span class="edited">Edited</span>', page)
        self.assertNotIn("see you at 5", page)                     # no history in the chat

    def test_only_the_sender_can_edit_and_only_while_they_can_write(self):
        mine = self.say(self.a, "hello")
        self.assertEqual(self.edit(mine, "hacked", client=self.other).status_code, 404)
        self.assertEqual(self.edit(mine, "   ").status_code, 400)
        Block.objects.create(blocker=self.b, blocked=self.a, messages=True)
        self.assertEqual(self.edit(mine, "changed").status_code, 400)
        mine.refresh_from_db()
        self.assertEqual(mine.text, "hello")
        self.assertEqual(self.delete(mine).status_code, 200)       # deleting your own still works
        mine.refresh_from_db()
        self.assertTrue(mine.is_deleted)

    def test_delete_shows_message_deleted_but_keeps_it(self):
        mine = self.say(self.a, "something I regret")
        self.assertEqual(self.delete(mine).status_code, 200)
        mine.refresh_from_db()
        self.assertTrue(mine.is_deleted)
        self.assertEqual(mine.text, "something I regret")            # still in the database
        page = self.room(self.other)
        self.assertIn("Message deleted", page)
        self.assertNotIn("something I regret", page)
        self.assertIn(f'data-report="{reverse("report_message", args=[mine.pk])}"', page)   # still reportable
        inbox = self.other.get(reverse("inbox") + "?tab=inbox").content.decode()
        self.assertIn("Message deleted", inbox)
        self.assertNotIn("something I regret", inbox)
        self.assertEqual(self.edit(mine, "new text").status_code, 400)
        self.assertEqual(self.delete(mine, client=self.other).status_code, 404)

    def test_a_quote_of_a_deleted_message_says_so(self):
        mine = self.say(self.a, "private thing")
        self.say(self.b, "what do you mean?", reply_to=mine)
        self.delete(mine)
        page = self.room(self.other)
        self.assertNotIn("private thing", page)
        self.assertEqual(page.count("Message deleted"), 2)            # the message and the quote of it

    def test_open_chats_pick_up_edits_and_deletes(self):
        mine = self.say(self.a, "first")
        quoting = self.say(self.b, "about that", reply_to=mine)
        page = self.other.get(reverse("chat_room", args=[self.conv.pk]))
        cursor, last = page.context["changes_cursor"], page.context["last_id"]
        poll = reverse("chat_poll", args=[self.conv.pk])
        self.assertEqual(self.other.get(poll, {"after": last, "changed": cursor}).json()["changed"], "")
        self.edit(mine, "first (fixed)")
        data = self.other.get(poll, {"after": last, "changed": cursor}).json()
        self.assertIn("first (fixed)", data["changed"])
        self.assertIn(f'data-id="{quoting.pk}"', data["changed"])    # its quote changed too
        self.assertNotIn("first (fixed)", data["html"])              # not a new message, no sound
        later = data["cursor"] + 10
        self.assertEqual(self.other.get(poll, {"after": last, "changed": later}).json()["changed"], "")

    def test_a_deleted_photo_is_no_longer_served(self):
        from django.core.files.base import ContentFile
        with self.settings(MEDIA_ROOT="/tmp/nitsdate-test-media"):
            pic = self.say(self.a, "")
            pic.image.save("p.jpg", ContentFile(b"\xff\xd8\xff"), save=True)
            self.delete(pic)
            self.assertEqual(self.other.get(reverse("chat_photo", args=[pic.pk])).status_code, 404)

    def test_reports_show_deleted_and_edited_text_to_moderators(self):
        from moderation.models import ModeratorGrant, Report
        mine = self.say(self.a, "you are awful")
        self.edit(mine, "you are great")
        self.delete(mine)
        form = self.other.get(reverse("report_message", args=[mine.pk])).content.decode()
        self.assertIn("Message deleted", form)                        # the reporter sees what the chat showed
        self.assertNotIn("you are awful", form)
        self.other.post(reverse("report_message", args=[mine.pk]), {"reason": Report.REASONS[0][0]})
        report = Report.objects.get()
        self.assertIn("you are great", report.snapshot)
        self.assertIn("you are awful", report.snapshot)
        self.assertIn("Deleted by the sender", report.snapshot)

        mod = make_user("Mona Das")
        ModeratorGrant.objects.create(email=mod.email)
        self.client.force_login(mod)
        page = self.client.get(reverse("mod_report", args=[report.pk])).content.decode()
        self.assertIn("Deleted by the sender", page)
        self.assertIn("deleted by sender", page)                     # in the chat context
        self.assertIn("edited 1 time", page)
        self.assertIn("you are awful", page)
        self.client.post(reverse("mod_report", args=[report.pk]), {"action": "remove"})
        mine.refresh_from_db()
        self.assertTrue(mine.removed)
        self.assertEqual((mine.text, mine.edits.count()), ("", 0))     # a moderator's removal wipes it
