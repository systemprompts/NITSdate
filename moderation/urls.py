from django.urls import path

from . import views

urlpatterns = [
    path("report/post/<int:pk>/", views.report_post, name="report_post"),
    path("report/comment/<int:pk>/", views.report_comment, name="report_comment"),
    path("report/message/<int:pk>/", views.report_message, name="report_message"),
    path("report/profile/<int:user_id>/", views.report_profile, name="report_profile"),
    path("report/chat/<int:pk>/", views.report_chat, name="report_chat"),
    path("mod/", views.reports, name="mod_reports"),
    path("mod/reports/<int:pk>/", views.report_detail, name="mod_report"),
    path("mod/users/", views.users, name="mod_users"),
    path("mod/users/<int:user_id>/", views.user_detail, name="mod_user"),
    path("mod/users/<int:user_id>/photo/", views.mod_profile_photo, name="mod_profile_photo"),
    path("mod/photos/<int:pk>/", views.mod_gallery_photo, name="mod_gallery_photo"),
    path("mod/messages/<int:pk>/image/", views.mod_message_image, name="mod_message_image"),
    path("mod/admin/", views.admin_page, name="mod_admin"),
]
