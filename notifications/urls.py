from django.urls import path

from . import views

urlpatterns = [
    path("", views.notifications, name="notifications"),
    path("settings/", views.notification_settings, name="notification_settings"),
]
