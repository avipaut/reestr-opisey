from django.contrib import admin

from .models import Inventory, Item, ItemStatusChange, ObjectType, Reconciliation, Rule, SourceFile, Status


@admin.register(Status)
class StatusAdmin(admin.ModelAdmin):
    list_display = ["name", "order", "is_active"]
    list_editable = ["order", "is_active"]


@admin.register(ObjectType)
class ObjectTypeAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(SourceFile)
class SourceFileAdmin(admin.ModelAdmin):
    list_display = ["name", "rel_folder", "size", "mtime", "last_seen", "missing"]
    search_fields = ["name", "path"]
    list_filter = ["missing"]
    readonly_fields = [f.name for f in SourceFile._meta.fields]


class RuleInline(admin.TabularInline):
    model = Rule
    extra = 0
    readonly_fields = ["order", "field", "text"]


class ReconciliationInline(admin.TabularInline):
    model = Reconciliation
    extra = 0
    readonly_fields = ["account", "src_initial", "sum_initial", "src_dep", "sum_dep", "matches"]


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ["source_file", "node", "state", "is_current", "rows", "qty", "parsed_at", "valid_from", "valid_to"]
    list_filter = ["state", "is_current", "check_flag"]
    search_fields = ["source_file__name", "org_text", "node__name", "node__path"]
    raw_id_fields = ["node", "source_file", "decided_by"]
    readonly_fields = ["sha256", "content_hash", "parsed_at", "decided_at", "valid_from", "valid_to"]
    inlines = [ReconciliationInline, RuleInline]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["name", "inv_no", "recommended_code", "status", "qty", "cost_initial", "inventory"]
    list_filter = ["status", "sheet"]
    search_fields = ["name", "inv_no", "model"]
    raw_id_fields = ["inventory"]
    list_select_related = ["status", "inventory"]


@admin.register(ItemStatusChange)
class ItemStatusChangeAdmin(admin.ModelAdmin):
    list_display = ["item", "old", "new", "by", "at"]
    raw_id_fields = ["item"]
