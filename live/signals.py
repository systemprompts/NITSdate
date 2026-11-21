"""Changes made with .save()/.create() publish from here. Places that use queryset .update()
(which sends no signals) call live.publish themselves."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from accounts.models import Block, ConnectionRequest
from chat.models import Message, PriorityRequest
from moderation.models import Report
from notifications.models import Notification

from . import publish


@receiver(post_save, sender=Message)
def message_saved(sender, instance, created, **kwargs):
    if created:   # also system messages: matched, connected, priority, left, account deleted
        publish.push_conversation(instance.conversation)


@receiver(post_save, sender=ConnectionRequest)
def connection_request_saved(sender, instance, **kwargs):
    if instance.conversation_id:
        publish.push_conversation(instance.conversation)
    publish.push_badges(instance.from_user_id, instance.to_user_id)


@receiver(post_save, sender=PriorityRequest)
def priority_request_saved(sender, instance, **kwargs):
    publish.push_conversation(instance.conversation)


@receiver(post_save, sender=Block)
@receiver(post_delete, sender=Block)
def block_changed(sender, instance, **kwargs):
    publish.push_pair(instance.blocker_id, instance.blocked_id)


@receiver(post_save, sender=Notification)
def notification_saved(sender, instance, created, **kwargs):
    if created:
        publish.push_badges(instance.recipient_id)


@receiver(post_save, sender=Report)
def report_saved(sender, instance, **kwargs):
    publish.push_mods()
