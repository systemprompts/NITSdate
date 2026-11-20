from django.urls import path

from . import views

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("badges/", views.badges, name="nav_badges"),
    path("<int:pk>/", views.room, name="chat_room"),
    path("<int:pk>/poll/", views.poll, name="chat_poll"),
    path("<int:pk>/history/", views.history, name="chat_history"),
    path("<int:pk>/send/", views.send, name="chat_send"),
    path("<int:pk>/connect/", views.request_connection, name="chat_connect"),
    path("<int:pk>/priority/", views.request_priority, name="chat_priority"),
    path("<int:pk>/priority/<int:req_id>/<str:action>/", views.priority_action, name="chat_priority_action"),
    path("<int:pk>/leave/", views.leave, name="chat_leave"),
    path("photo/<int:msg_id>/", views.photo, name="chat_photo"),
]
