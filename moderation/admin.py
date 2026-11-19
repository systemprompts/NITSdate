from django.contrib import admin

from .models import ModAction, ModeratorGrant, Report, Restriction

admin.site.register(ModeratorGrant)
admin.site.register(Restriction)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("kind", "reason", "reporter", "reported_user", "status", "created_at")
    list_filter = ("kind", "status", "reason")


@admin.register(ModAction)
class ModActionAdmin(admin.ModelAdmin):
    list_display = ("mod", "action", "target_user", "created_at")
