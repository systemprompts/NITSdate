from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.http import Http404, HttpResponse
from django.urls import include, path, re_path
from django.views.decorators.cache import never_cache
from django.views.generic import RedirectView, TemplateView

from notifications import views as notification_views


def robots_txt(request):
    # a private campus community: keep every page out of search engines
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


@never_cache
def healthz(request):
    connection.ensure_connection()
    return HttpResponse("ok", content_type="text/plain")


def not_found(request, *args, **kwargs):
    raise Http404


urlpatterns = [
    path("robots.txt", robots_txt),
    path("sw.js", notification_views.service_worker, name="service_worker"),
    path("manifest.webmanifest", notification_views.manifest, name="manifest"),
    path("favicon.ico", RedirectView.as_view(url=settings.STATIC_URL + "img/favicon.svg", permanent=True)),
    path("healthz", healthz),
    path("privacy/", TemplateView.as_view(template_name="legal/privacy.html",
                                          extra_context={"contact_email": settings.CONTACT_EMAIL}), name="privacy"),
    path("terms/", TemplateView.as_view(template_name="legal/terms.html",
                                        extra_context={"contact_email": settings.CONTACT_EMAIL}), name="terms"),
    path(settings.ADMIN_URL, admin.site.urls),
    # Sign-in is Google-only: close allauth's password / email / code pages entirely.
    re_path(r"^accounts/(password|email|confirm-email|reauthenticate|login/code|2fa)(/.*)?$", not_found),
    path("accounts/3rdparty/", not_found),
    # Google is the only sign-in method: send allauth's own pages to ours.
    path("accounts/login/", RedirectView.as_view(pattern_name="login", query_string=True)),
    path("accounts/signup/", RedirectView.as_view(pattern_name="login")),
    path("accounts/", include("allauth.urls")),
    path("", include("accounts.urls")),
    path("", include("feed.urls")),
    path("messages/", include("chat.urls")),
    path("match/", include("matchmaking.urls")),
    path("notifications/", include("notifications.urls")),
    path("", include("moderation.urls")),
]
