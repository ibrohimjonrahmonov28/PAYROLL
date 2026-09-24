from django.urls import path
from . import views
from . import terminal_views
from . import control_views

app_name = 'production'

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('orders/', views.order_list_view, name='order_list'),
    path('orders/<int:order_id>/', views.order_detail_view, name='order_detail'),
    path('orders/<int:order_id>/print-all/', views.order_print_all_stickers_view, name='order_print_all_stickers'),
    path('orders/<int:order_id>/pdf/', views.order_download_all_stickers_100x60_pdf, name='order_download_all_stickers_pdf'),
    path('batches/<int:batch_id>/passport/', views.pastal_passport_view, name='pastal_passport'),
    path('boxes/<int:box_id>/split/', views.box_split_wizard_view, name='box_split_wizard'),
    path('boxes/<int:box_id>/print/', views.box_print_stickers_view, name='box_print_stickers'),
    path('boxes/<int:box_id>/pdf/', views.box_download_stickers_100x60_pdf, name='box_download_stickers_pdf'),
    path('articles/', views.articles_catalog_view, name='articles_catalog'),
    path('statistics/', views.box_pipeline_statistics_view, name='statistics_pipeline'),
    path('statistics/api/ticket/<str:code_or_id>/', views.api_ticket_scan_detail, name='api_ticket_scan_detail'),

    # Master Skanerlash Terminali (Zebra DS22)
    path('terminal/', terminal_views.terminal_home_view, name='terminal_home'),
    path('terminal/api/identify-worker/', terminal_views.terminal_identify_worker_api, name='terminal_identify_worker'),
    path('terminal/api/scan-ticket/', terminal_views.terminal_scan_ticket_api, name='terminal_scan_ticket'),
    path('terminal/api/remove-ticket/', terminal_views.terminal_remove_ticket_api, name='terminal_remove_ticket'),
    path('terminal/api/finalize/', terminal_views.terminal_finalize_api, name='terminal_finalize'),
    path('terminal/api/box-lookup/', terminal_views.terminal_box_lookup_api, name='terminal_box_lookup'),
    path('terminal/api/worker-balance/', terminal_views.terminal_worker_balance_api, name='terminal_worker_balance'),
    path('terminal/api/reset-session/', terminal_views.terminal_reset_session_api, name='terminal_reset_session'),

    # Sifat Nazorati (OTK / Control)
    path('control/', control_views.control_home_view, name='control_home'),
]
