import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from . import push, services
from .models import PushSubscription

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


# ---- push notifications ---------------------------------------------------------------------------

@login_required
@require_POST
def push_subscribe(request):
    """This browser agreed to notifications: remember where to send them (and for whom). Sent again by
    open pages now and then, so a browser that someone else signs in to switches to them."""
    if not push.enabled():
        return JsonResponse({"ok": False}, status=404)
    try:
        data = json.loads(request.body)
        endpoint, keys = data["endpoint"], data["keys"]
        p256dh, auth = keys["p256dh"], keys["auth"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False}, status=400)
    if not (isinstance(endpoint, str) and isinstance(p256dh, str) and isinstance(auth, str)
            and len(endpoint) <= 2000 and len(p256dh) <= 200 and len(auth) <= 100 and push.endpoint_allowed(endpoint)):
        return JsonResponse({"ok": False}, status=400)
    PushSubscription.objects.update_or_create(key=push.endpoint_key(endpoint), defaults={
        "user": request.user, "endpoint": endpoint, "p256dh": p256dh, "auth": auth,
        "session_key": request.session.session_key or ""})
    extra = PushSubscription.objects.filter(user=request.user).values_list("pk", flat=True)[push.MAX_PER_USER:]
    PushSubscription.objects.filter(pk__in=list(extra)).delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def push_unsubscribe(request):
    try:
        endpoint = json.loads(request.body)["endpoint"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False}, status=400)
    PushSubscription.objects.filter(key=push.endpoint_key(str(endpoint)), user=request.user).delete()
    return JsonResponse({"ok": True})


def service_worker(request):
    """/sw.js: shows the notifications. Served from the site root so it covers every page; never cached,
    so a new version reaches browsers straight away."""
    response = HttpResponse(render_to_string("push/sw.js"), content_type="application/javascript")
    response["Cache-Control"] = "no-store, max-age=0"
    return response


def manifest(request):
    """Lets phones add nitsdate to the home screen (on iPhones that's needed for notifications)."""
    response = HttpResponse(render_to_string("push/manifest.webmanifest"), content_type="application/manifest+json")
    response["Cache-Control"] = "public, max-age=3600"
    return response
