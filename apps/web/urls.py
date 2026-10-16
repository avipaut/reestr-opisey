from django.urls import path

from . import views, views_folders, views_items, views_nodes, views_scan

urlpatterns = [
    path("", views.review_list, name="review_list"),
    path("scan/", views.scan_now, name="scan_now"),
    path("review/<int:pk>/", views.review_detail, name="review_detail"),
    path("review/<int:pk>/decide/", views.review_decide, name="review_decide"),
    path("review/bulk/", views.review_bulk, name="review_bulk"),
    path("stats/codes/", views.stats_codes, name="stats_codes"),
    path("stats/orgs/", views.stats_orgs, name="stats_orgs"),
    path("scans/", views.scans, name="scans"),
    path("scans/<int:pk>/", views.scan_detail, name="scan_detail"),
    path("scans/<int:pk>/progress/", views_scan.scan_progress, name="scan_progress"),
    path("scans/<int:pk>/tree/", views_scan.scan_tree, name="scan_tree"),
    path("import/fs/", views_folders.fs_list, name="fs_list"),
    path("import/folder/", views_folders.import_folder, name="import_folder"),
    path("import/", views_items.import_page, name="import_page"),
    path("inventories/", views_items.inventories, name="inventories"),
    path("inventories/<int:pk>/", views_items.inventory_detail, name="inventory_detail"),
    path("items/", views_items.items_search, name="items_search"),
    path("items/<int:pk>/", views_items.item_detail, name="item_detail"),
    path("items/<int:pk>/status/", views_items.item_status, name="item_status"),
    path("nodes/", views_nodes.nodes_page, name="nodes"),
    path("nodes/children/", views_nodes.nodes_children, name="nodes_children"),
    path("nodes/search/", views_nodes.nodes_search, name="nodes_search"),
    path("nodes/add/", views_nodes.node_add, name="node_add"),
    path("nodes/<int:pk>/edit/", views_nodes.node_edit, name="node_edit"),
]
