from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from .adapters import NOT_ALLOWED, email_allowed, may_sign_in

EXEMPT_PREFIXES = ("/onboarding/", "/logout/", "/login/", "/dev-login/", "/accounts/", "/static/",
                   "/privacy/", "/terms/", "/healthz", "/robots.txt", "/favicon.ico", "/" + settings.ADMIN_URL)


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
                messages.error(request, NOT_ALLOWED.format(domain=settings.ALLOWED_EMAIL_DOMAIN))
                return redirect("login")
            if not request.path.startswith(EXEMPT_PREFIXES) and not user.has_profile:
                return redirect(reverse("onboarding"))
        return self.get_response(request)
