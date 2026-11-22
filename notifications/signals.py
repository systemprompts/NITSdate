from django.contrib.auth.signals import user_logged_out
from django.dispatch import receiver

from .models import PushSubscription


@receiver(user_logged_out)
def stop_pushing_to_this_browser(sender, request, user, **kwargs):
    """Signing out stops notifications to this browser, so the next person to use it doesn't get yours.
    Signing in again turns them back on by itself (the browser has already said yes)."""
    key = request.session.session_key if request is not None and hasattr(request, "session") else None
    if key:
        PushSubscription.objects.filter(session_key=key).delete()
