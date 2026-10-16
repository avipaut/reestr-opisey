from django.urls import path

from . import views

urlpatterns = [
    path("", views.review_list, name="review_list"),
    path("scan/", views.scan_now, name="scan_now"),
    path("review/<int:pk>/", views.review_detail, name="review_detail"),
    path("review/<int:pk>/decide/", views.review_decide, name="review_decide"),
    path("stats/codes/", views.stats_codes, name="stats_codes"),
    path("stats/orgs/", views.stats_orgs, name="stats_orgs"),
    path("scans/", views.scans, name="scans"),
]
