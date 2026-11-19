import math

from django.conf import settings
from django.db import models
from django.utils import timezone


def hot_score(score, created_at):
    """Reddit's classic "hot" rank. Stored on the post so the feed can sort and
    page in the database instead of loading posts into Python."""
    order = math.log10(max(abs(score), 1))
    sign = 1 if score > 0 else -1 if score < 0 else 0
    return round(sign * order + created_at.timestamp() / 45000, 7)


class Post(models.Model):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    image = models.ImageField(upload_to="posts/%Y/%m/", blank=True)
    score = models.IntegerField(default=0, db_index=True)
    hot = models.FloatField(default=0, db_index=True)
    comment_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["score", "created_at"])]  # the "Top" feed

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.created_at is None:
            self.created_at = timezone.now()
        self.hot = hot_score(self.score, self.created_at)
        super().save(*args, **kwargs)

    @classmethod
    def set_score(cls, pk, score):
        """Update score and hot rank together (used after votes change)."""
        created_at = cls.objects.filter(pk=pk).values_list("created_at", flat=True).first()
        if created_at is not None:
            cls.objects.filter(pk=pk).update(score=score, hot=hot_score(score, created_at))


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies")
    body = models.TextField()
    score = models.IntegerField(default=0)
    is_deleted = models.BooleanField(default=False)
    removed = models.BooleanField(default=False, help_text="Removed by a moderator")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            # same column order and directions as the page's sort, so MySQL reads 30 entries instead of sorting
            models.Index(fields=["post", "parent", "-score", "created_at"], name="comment_rank_idx"),
        ]


class PostVote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="votes")
    value = models.SmallIntegerField()  # +1 / -1

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "post"], name="uniq_post_vote")]


class CommentVote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="votes")
    value = models.SmallIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "comment"], name="uniq_comment_vote")]
