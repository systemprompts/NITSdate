from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect


def email_allowed(email):
    """ALLOWED_EMAIL_DOMAIN=nits.ac.in accepts x@nits.ac.in and x@cse.nits.ac.in."""
    domain = settings.ALLOWED_EMAIL_DOMAIN
    if not domain:
        return True
    email = (email or "").strip().lower()
    return email.endswith("@" + domain) or email.endswith("." + domain)


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False  # no email/password accounts; Google only


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def pre_social_login(self, request, sociallogin):
        email = sociallogin.user.email or sociallogin.account.extra_data.get("email", "")
        if not email_allowed(email):
            messages.error(request, f"Please sign in with your college Google account (ending in {settings.ALLOWED_EMAIL_DOMAIN}).")
            raise ImmediateHttpResponse(redirect("login"))
        if sociallogin.account.extra_data.get("email_verified") is False:
            messages.error(request, "Your Google email address isn't verified.")
            raise ImmediateHttpResponse(redirect("login"))

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        user.set_unusable_password()
        return user
