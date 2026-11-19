"""Keep cached scores right when votes disappear without going through the vote
view, e.g. when an account (and so its votes) is deleted."""
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Comment, CommentVote, Post, PostVote


@receiver(post_delete, sender=PostVote)
def _post_vote_deleted(sender, instance, **kwargs):
    score = PostVote.objects.filter(post_id=instance.post_id).aggregate(s=Coalesce(Sum("value"), 0))["s"]
    Post.set_score(instance.post_id, score)


@receiver(post_delete, sender=CommentVote)
def _comment_vote_deleted(sender, instance, **kwargs):
    score = CommentVote.objects.filter(comment_id=instance.comment_id).aggregate(s=Coalesce(Sum("value"), 0))["s"]
    Comment.objects.filter(pk=instance.comment_id).update(score=score)
