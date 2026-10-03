from django.urls import path
from . import views
from . import terminal_views
from . import control_views
from . import sewing_statistics_views
from . import passport_views

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
    path('terminal/api/revoke-ticket/', terminal_views.terminal_revoke_ticket_api, name='terminal_revoke_ticket'),
    path('terminal/api/ticket-info/', terminal_views.terminal_ticket_info_api, name='terminal_ticket_info'),

    # Sifat Nazorati (OTK / Control)
    path('control/', control_views.control_home_view, name='control_home'),

    # Tikim Jarayoni Statistikasi (Umumiy Zakaz, Modellar va Razmerlar bo'yicha)
    path('sewing-statistics/', sewing_statistics_views.sewing_statistics_orders_view, name='sewing_statistics_orders'),
    path('sewing-statistics/close-all-unprinted/', sewing_statistics_views.sewing_statistics_close_all_unprinted_boxes_view, name='sewing_statistics_close_all_unprinted_boxes'),
    path('sewing-statistics/repairs/', sewing_statistics_views.sewing_statistics_repairs_view, name='sewing_statistics_repairs'),
    path('sewing-statistics/orders/<int:order_id>/', sewing_statistics_views.sewing_statistics_order_models_view, name='sewing_statistics_order_models'),
    path('sewing-statistics/orders/<int:order_id>/close-unprinted/', sewing_statistics_views.sewing_statistics_close_order_unprinted_boxes_view, name='sewing_statistics_close_order_unprinted_boxes'),
    path('sewing-statistics/orders/<int:order_id>/items/<int:order_item_id>/', sewing_statistics_views.sewing_statistics_model_detail_view, name='sewing_statistics_model_detail'),
    path('sewing-statistics/api/size-boxes/', sewing_statistics_views.api_sewing_statistics_size_boxes, name='api_sewing_statistics_size_boxes'),

    # Pastal Pasporti va Stikerlarni Tekshirish (Web & Webhook)
    path('passport-checker/', passport_views.passport_checker_web_view, name='passport_checker'),
    path('passport-checker/api/', passport_views.passport_checker_api, name='passport_checker_api'),
    path('passport-bot/webhook/', passport_views.passport_telegram_webhook, name='passport_telegram_webhook'),
]

