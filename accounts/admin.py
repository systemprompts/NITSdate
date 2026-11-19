from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Block, Connection, ConnectionRequest, GalleryPhoto, Profile, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "alias", "is_staff", "date_joined")
    search_fields = ("username", "email", "alias")
    fieldsets = BaseUserAdmin.fieldsets + (("nitsdate", {"fields": ("alias",)}),)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "gender", "age", "likes", "profile_visible", "year")
    search_fields = ("name", "user__email", "user__alias")


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ("user1", "user2", "created_at")


@admin.register(ConnectionRequest)
class ConnectionRequestAdmin(admin.ModelAdmin):
    list_display = ("from_user", "to_user", "source", "status", "created_at")
    list_filter = ("source", "status")


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = ("blocker", "blocked", "messages", "profile", "updated_at")


@admin.register(GalleryPhoto)
class GalleryPhotoAdmin(admin.ModelAdmin):
    list_display = ("user", "privacy", "created_at")
