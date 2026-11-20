from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import services

SETTINGS = [
    ("notify_upvotes", "Upvotes", "When someone upvotes your post or comment. It never says who."),
    ("notify_comments", "Comments", "When someone comments on your post."),
    ("notify_replies", "Replies", "When someone replies to your comment."),
]


@login_required
def notifications(request):
    items = services.grouped(request.user)
    response = render(request, "notifications/list.html", {
        "items": items,
        "settings_rows": [{"key": k, "label": label, "help": help_text, "on": getattr(request.user, k)}
                          for k, label, help_text in SETTINGS],
    })
    services.mark_all_read(request.user)   # after rendering, so new ones are highlighted this once
    return response


@login_required
@require_POST
def notification_settings(request):
    user = request.user
    for key, _, _ in SETTINGS:
        setattr(user, key, request.POST.get(key) == "on")
    user.save(update_fields=[k for k, _, _ in SETTINGS])
    messages.success(request, "Notification settings saved.")
    return redirect("notifications")
