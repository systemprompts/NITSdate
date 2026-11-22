from django.urls import path

from . import views

urlpatterns = [
    path("", views.match_home, name="match"),
    path("start/", views.start, name="match_start"),
    path("cancel/", views.cancel, name="match_cancel"),
    path("fast/", views.fast_queue, name="match_fast"),
    path("status/", views.status, name="match_status"),
]
