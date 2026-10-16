from django.contrib import admin

from .models import VersionStat


@admin.register(VersionStat)
class VersionStatAdmin(admin.ModelAdmin):
    list_display = ["inventory", "node", "code", "status", "rows", "qty", "valid_from", "valid_to"]
    list_filter = ["status"]
    raw_id_fields = ["inventory", "node"]
