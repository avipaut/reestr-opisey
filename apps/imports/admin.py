from django.contrib import admin

from .models import ScanRun


@admin.register(ScanRun)
class ScanRunAdmin(admin.ModelAdmin):
    list_display = ["started_at", "state", "found", "unchanged", "moved", "parsed", "pending",
                    "same_content", "errors", "missing", "finished_at"]
    list_filter = ["state"]
    readonly_fields = [f.name for f in ScanRun._meta.fields]
