from django.contrib import admin

from .models import FolderMapping, Node


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ["path", "code", "name", "level", "parent"]
    search_fields = ["code", "name", "path"]
    list_filter = ["level"]
    raw_id_fields = ["parent"]
    readonly_fields = ["path", "level", "created_at", "created_by"]


@admin.register(FolderMapping)
class FolderMappingAdmin(admin.ModelAdmin):
    list_display = ["folder", "node", "created_at"]
    search_fields = ["folder"]
    raw_id_fields = ["node"]
