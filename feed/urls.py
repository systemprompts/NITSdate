from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("all/", views.all_nits, name="all_nits"),
    path("posts/new/", views.new_post, name="new_post"),
    path("posts/<int:pk>/", views.post_detail, name="post_detail"),
    path("posts/<int:pk>/vote/", views.vote_post, name="vote_post"),
    path("posts/<int:pk>/comment/", views.add_comment, name="add_comment"),
    path("posts/<int:pk>/delete/", views.delete_post, name="delete_post"),
    path("posts/<int:pk>/image/", views.post_image, name="post_image"),
    path("comments/<int:pk>/vote/", views.vote_comment, name="vote_comment"),
    path("comments/<int:pk>/delete/", views.delete_comment, name="delete_comment"),
]
