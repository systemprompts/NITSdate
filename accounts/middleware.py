from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

EXEMPT_PREFIXES = ("/onboarding/", "/logout/", "/login/", "/dev-login/", "/accounts/", "/static/",
                   "/privacy/", "/terms/", "/healthz", "/robots.txt", "/" + settings.ADMIN_URL)


class OnboardingMiddleware:
    """Signed-in users must finish the short sign-up form before anything else."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not request.path.startswith(EXEMPT_PREFIXES) and not user.has_profile:
            return redirect(reverse("onboarding"))
        return self.get_response(request)
