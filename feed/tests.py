"""Scale tests: pages must load a bounded amount of data no matter how big things get."""
import re
import time
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import Connection, Profile, User
from accounts.tests import make_user
from chat.models import Conversation, Message
from chat.services import connect_users, create_match_conversation

from .models import Comment, Post, hot_score


def timed_get(client, url):
    client.get(url)  # warm-up: first render compiles templates
    with CaptureQueriesContext(connection) as q:
        start = time.perf_counter()
        resp = client.get(url)
        ms = (time.perf_counter() - start) * 1000
    return resp, len(q), ms


class ScaleTests(TestCase):
    report = []

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        print("\n" + "\n".join(cls.report))

    def setUp(self):
        self.a = make_user("Scale A", "female", 20, "male")
        self.b = make_user("Scale B", "male", 21, "female")

    def test_chat_with_10000_messages(self):
        conv = create_match_conversation(self.a, self.b)
        Message.objects.bulk_create([Message(conversation=conv, sender=self.a if i % 2 else self.b, stage="match",
                                             text=f"old {i}") for i in range(3000)])
        conv = connect_users(self.a, self.b, via_conversation=conv)
        Message.objects.bulk_create([Message(conversation=conv, sender=self.a if i % 2 else self.b, stage="inbox",
                                             text=f"msg {i}") for i in range(7000)])
        self.client.force_login(self.a)
        resp, queries, ms = timed_get(self.client, reverse("chat_room", args=[conv.pk]))
        html = resp.content.decode()
        shown = re.findall(r'class="msg (?:mine|theirs)" data-id', html)
        self.assertEqual(len(shown), 50)                 # only the newest 50 of 10,000
        self.assertIn("msg 6999", html)
        self.assertNotIn("msg 6949", html)
        self.assertNotIn("old 2999", html)               # archive not loaded until opened
        self.assertIn("Archived matchmaking chat (3000 messages)", html)
        self.assertIn('id="load-earlier"', html)
        self.assertLess(queries, 30)
        first = re.search(r'data-before="(\d+)"', html).group(1)
        older = self.client.get(reverse("chat_history", args=[conv.pk]) + f"?stage=inbox&before={first}").json()
        self.assertEqual(older["html"].count('data-id'), 50)
        self.assertTrue(older["has_more"])
        archive = self.client.get(reverse("chat_history", args=[conv.pk]) + "?stage=match").json()
        self.assertIn("old 2999", archive["html"])
        self.report.append(f"chat room, 10,000 messages: 50 shown, {queries} queries, {ms:.0f} ms")

    def test_feed_with_10000_posts(self):
        now = timezone.now()
        posts = []
        for i in range(10000):
            created = now - timedelta(minutes=i)
            score = (i * 37) % 200 - 50
            posts.append(Post(author=self.b, title=f"post {i}", score=score, created_at=created,
                              hot=hot_score(score, created), campus=self.b.campus))
        Post.objects.bulk_create(posts)
        self.client.force_login(self.a)
        expected = sorted(posts, key=lambda p: p.hot, reverse=True)[:20]
        for sort in ("hot", "new", "top"):
            resp, queries, ms = timed_get(self.client, f"/?sort={sort}")
            html = resp.content.decode()
            titles = re.findall(r'<h2 class="post-title"><a href="/posts/\d+/">(post \d+)</a>', html)
            self.assertEqual(len(titles), 20)
            if sort == "hot":
                self.assertEqual(titles, [p.title for p in expected])
            self.assertLess(queries, 25)
            self.report.append(f"feed ({sort}), 10,000 posts: 20 shown, {queries} queries, {ms:.0f} ms")
        resp = self.client.get("/?sort=hot&page=400")   # old posts are reachable too
        self.assertEqual(len(re.findall(r'class="post-title"', resp.content.decode())), 20)

    def test_post_with_10000_comments(self):
        post = Post.objects.create(author=self.b, title="busy post")
        Comment.objects.bulk_create([Comment(post=post, author=self.b, body=f"top {i}") for i in range(9000)])
        top = list(Comment.objects.filter(post=post).order_by("id")[:2])   # MySQL bulk_create returns no ids
        popular = top[0]
        Comment.objects.bulk_create([Comment(post=post, author=self.a, parent=popular, body=f"reply {i}")
                                     for i in range(400)])
        parent = top[1]
        for depth in range(10):                         # a 10-deep chain
            parent = Comment.objects.create(post=post, author=self.a, parent=parent, body=f"deep {depth}")
        Post.objects.filter(pk=post.pk).update(comment_count=Comment.objects.filter(post=post).count())
        self.client.force_login(self.a)
        resp, queries, ms = timed_get(self.client, reverse("post_detail", args=[post.pk]))
        html = resp.content.decode()
        rendered = len(re.findall(r'<div class="comment" id="c\d+"', html))
        self.assertLessEqual(rendered, 300)
        self.assertIn("Show ", html)                    # the 400-reply comment offers more
        self.assertIn("Continue this thread", html)     # the 10-deep chain stops at 6
        self.assertIn("More comments", html)
        self.assertLess(queries, 40)
        self.report.append(f"post page, {Comment.objects.count():,} comments: {rendered} shown, {queries} queries, {ms:.0f} ms")
        deep = Comment.objects.get(body="deep 5")
        thread = self.client.get(reverse("post_detail", args=[post.pk]) + f"?thread={deep.pk}").content.decode()
        self.assertIn("deep 9", thread)

    def test_people_and_inbox_with_many_connections(self):
        others = []
        for i in range(300):
            u = User.objects.create(username=f"bulk{i}", email=f"bulk{i}@cse.nits.ac.in")
            p = Profile(user=u, name=f"Bulk {i}", gender="male", likes="female")
            p.age = 20
            p.init_meta()
            others.append(p)
        Profile.objects.bulk_create(others)
        for p in others:
            connect_users(self.a, p.user)
        self.client.force_login(self.a)
        for url, per_page in ((reverse("people") + "?tab=connections", 40), (reverse("inbox") + "?tab=inbox", 30)):
            resp, queries, ms = timed_get(self.client, url)
            self.assertEqual(resp.status_code, 200)
            self.assertLess(queries, 60, url)           # no query per connection any more
            self.report.append(f"{url}, 300 connections: {per_page} per page, {queries} queries, {ms:.0f} ms")
