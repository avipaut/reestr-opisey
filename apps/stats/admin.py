from django.contrib import admin

from .models import CodeGroup, VersionStat


@admin.register(VersionStat)
class VersionStatAdmin(admin.ModelAdmin):
    list_display = ["inventory", "node", "code", "status", "rows", "qty", "valid_from", "valid_to"]
    list_filter = ["status"]
    raw_id_fields = ["inventory", "node"]


@admin.register(CodeGroup)
class CodeGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "prefixes", "order", "is_active"]
    list_editable = ["prefixes", "order", "is_active"]
