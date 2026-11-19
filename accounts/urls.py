from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_page, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dev-login/", views.dev_login, name="dev_login"),
    path("onboarding/", views.onboarding, name="onboarding"),
    path("me/", views.my_profile, name="my_profile"),
    path("me/visibility/", views.set_visibility, name="set_visibility"),
    path("me/field/<str:key>/", views.edit_field, name="edit_field"),
    path("me/field/<str:key>/privacy/", views.set_privacy, name="set_privacy"),
    path("u/<int:user_id>/", views.profile_view, name="profile"),
    path("u/<int:user_id>/photo/", views.profile_photo, name="profile_photo"),
    path("u/<int:user_id>/connect/", views.send_request, name="send_request"),
    path("u/<int:user_id>/block/", views.block_user, name="block_user"),
    path("messages/<int:pk>/block/", views.block_in_chat, name="block_in_chat"),
    path("u/<int:user_id>/photos/<int:pk>/", views.gallery_image, name="gallery_image"),
    path("me/photos/", views.gallery_upload, name="gallery_upload"),
    path("me/photos/<int:pk>/privacy/", views.gallery_privacy, name="gallery_privacy"),
    path("me/photos/<int:pk>/delete/", views.gallery_delete, name="gallery_delete"),
    path("me/photos/<int:pk>/avatar/", views.gallery_make_avatar, name="gallery_make_avatar"),
    path("people/", views.people, name="people"),
    path("requests/<int:pk>/<str:action>/", views.request_action, name="request_action"),
]
