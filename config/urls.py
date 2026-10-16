from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Выход — только POST (граф APCS: debt-logout-by-get); LogoutView Django 5 принимает только POST.
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("apps.web.urls")),
]
