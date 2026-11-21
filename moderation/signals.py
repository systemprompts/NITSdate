from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import FormerAccount, Restriction


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def carry_over_limits(sender, instance, created, **kwargs):
    """Someone signing up with the email of a deleted account gets that account's moderator limits
    back: deleting and coming back isn't a way around them."""
    if not created or not instance.email:
        return
    former = FormerAccount.objects.filter(email__iexact=instance.email.strip()).first()
    if former is None or not former.has_limits:
        return
    limits = {f: former.limits[f] for f in FormerAccount.LIMIT_FIELDS if f in former.limits}
    note = (limits.pop("note", "") or "").strip()
    carried = f"Carried over from deleted account #{former.last_account_id}."
    Restriction.objects.update_or_create(user=instance, defaults={**limits, "note": f"{note}\n{carried}".strip()})
