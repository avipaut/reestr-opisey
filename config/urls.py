from django.contrib import admin
from django.http import HttpResponsePermanentRedirect
from django.templatetags.static import static
from django.urls import include, path


def favicon(request):
    """Браузер просит /favicon.ico сам, если в странице иконки нет (админка). Адрес —
    при запросе: в боевом режиме имя файла берётся из манифеста собранной статики."""
    return HttpResponsePermanentRedirect(static("web/icon.svg"))


urlpatterns = [
    path("admin/", admin.site.urls),
    path("favicon.ico", favicon),
    # Выход — только POST (граф APCS: debt-logout-by-get); LogoutView Django 5 принимает только POST.
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("apps.web.urls")),
]
