from django.urls import path

from . import views

urlpatterns = [
    path("", views.notifications, name="notifications"),
    path("settings/", views.notification_settings, name="notification_settings"),
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
]
