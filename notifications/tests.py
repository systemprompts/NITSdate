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
