from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect

from .campuses import campus_of_email, is_open


def email_allowed(email):
    """A college address of a NIT that's open for sign-up (settings.OPEN_CAMPUSES), including its
    departmental subdomains: x@nits.ac.in, x@cse.nits.ac.in, x@student.nitw.ac.in ..."""
    if settings.OPEN_CAMPUSES == "any":
        return True
    return is_open(campus_of_email(email))


def may_sign_in(email):
    """A college address, or one the moderators let in: allowlisted emails, moderators and admins."""
    if email_allowed(email):
        return True
    from moderation.models import AllowedEmail, ModeratorGrant  # avoid an import cycle at startup
    from accounts.models import User
    email = (email or "").strip().lower()
    if not email:
        return False
    return (email in settings.ADMIN_EMAILS
            or AllowedEmail.objects.filter(email=email).exists()
            or ModeratorGrant.objects.filter(email=email).exists()
            or User.objects.filter(email__iexact=email, is_superuser=True, is_active=True).exists())


NOT_ALLOWED = "Please sign in with your college Google account from one of the NITs listed below."


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False  # no email/password accounts; Google only


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def pre_social_login(self, request, sociallogin):
        email = sociallogin.user.email or sociallogin.account.extra_data.get("email", "")
        if not may_sign_in(email):
            messages.error(request, NOT_ALLOWED)
            raise ImmediateHttpResponse(redirect("login"))
        if sociallogin.account.extra_data.get("email_verified") is False:
            messages.error(request, "Your Google email address isn't verified.")
            raise ImmediateHttpResponse(redirect("login"))

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        user.set_unusable_password()
        return user
