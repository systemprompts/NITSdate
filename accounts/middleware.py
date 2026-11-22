from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from .adapters import NOT_ALLOWED, email_allowed, may_sign_in
from .models import User

EXEMPT_PREFIXES = ("/onboarding/", "/logout/", "/login/", "/dev-login/", "/accounts/", "/static/",
                   "/privacy/", "/terms/", "/healthz", "/robots.txt", "/favicon.ico", "/sw.js", "/manifest.webmanifest",
                   "/" + settings.ADMIN_URL)


class OnboardingMiddleware:
    """Signed-in users must still be allowed in (college email or allowlisted) and must
    finish the short sign-up form before anything else."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not request.path.startswith(("/static/", "/healthz")):
            # the college-email rule also applies to people who signed in before it was switched on,
            # or whose allowlist entry a moderator removed (college addresses never need a query)
            if not (email_allowed(user.email) or user.is_superuser or may_sign_in(user.email)):
                logout(request)
                messages.error(request, NOT_ALLOWED)
                return redirect("login")
            if not request.path.startswith(EXEMPT_PREFIXES) and not user.has_profile:
                return redirect(reverse("onboarding"))
        return self.get_response(request)


class LastSeenMiddleware:
    """Notes when a signed-in person was last on the site, at most once a minute, so matchmaking
    can pair people who are around first."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_active:
            now = timezone.now()
            if user.last_seen is None or now - user.last_seen > timedelta(minutes=1):
                User.objects.filter(pk=user.pk).update(last_seen=now)
        return response
