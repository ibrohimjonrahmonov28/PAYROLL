from django.urls import path
from . import superadmin_views

urlpatterns = [
    path('', superadmin_views.superadmin_dashboard, name='superadmin_dashboard'),
    path('users/', superadmin_views.superadmin_users, name='superadmin_users'),
    path('users/<int:user_id>/badge/', superadmin_views.superadmin_user_badge, name='superadmin_user_badge'),
    path('users/print-badges/', superadmin_views.superadmin_users_print_badges, name='superadmin_users_print_badges'),
    path('users/download-badges-pdf/', superadmin_views.superadmin_users_download_badges_pdf, name='superadmin_users_download_badges_pdf'),
    path('payroll/', superadmin_views.superadmin_payroll, name='superadmin_payroll'),
    path('payroll/payout/', superadmin_views.superadmin_payout_create, name='superadmin_payout_create'),
    path('payroll/export/', superadmin_views.superadmin_payroll_export_csv, name='superadmin_payroll_export'),
    path('payroll/telegram-report/', superadmin_views.superadmin_send_telegram_report, name='superadmin_send_telegram_report'),
    path('payroll/download-daily-excel/', superadmin_views.superadmin_download_daily_excel, name='superadmin_download_daily_excel'),
    path('payroll/close-day/', superadmin_views.superadmin_close_daily_now, name='superadmin_close_daily_now'),
    path('payroll/worker/<int:worker_id>/daily/', superadmin_views.superadmin_worker_daily_breakdown, name='superadmin_worker_daily_breakdown'),
    path('workers/<int:worker_id>/history/', superadmin_views.superadmin_worker_history, name='superadmin_worker_history'),
    path('workers/<int:worker_id>/tickets-by-date/', superadmin_views.api_worker_tickets_by_date, name='api_worker_tickets_by_date'),
    path('payroll/bulk-pay/', superadmin_views.superadmin_payroll_bulk_pay, name='superadmin_payroll_bulk_pay'),
    path('pricing/', superadmin_views.superadmin_pricing, name='superadmin_pricing'),
    path('orders/', superadmin_views.superadmin_orders_list, name='superadmin_orders_list'),
    path('orders/<int:order_id>/', superadmin_views.superadmin_order_detail, name='superadmin_order_detail'),
    path('orders/<int:order_id>/edit/', superadmin_views.superadmin_order_edit, name='superadmin_order_edit'),
    path('orders/<int:order_id>/add-model/', superadmin_views.superadmin_order_add_model, name='superadmin_order_add_model'),
    path('orders/<int:order_id>/models/<int:item_id>/edit/', superadmin_views.superadmin_order_model_edit, name='superadmin_order_model_edit'),
    path('orders/<int:order_id>/models/<int:item_id>/delete/', superadmin_views.superadmin_order_model_delete, name='superadmin_order_model_delete'),
    path('orders/<int:order_id>/models/<int:item_id>/update-op/', superadmin_views.superadmin_order_model_update_operation, name='superadmin_order_model_update_operation'),
    path('orders/<int:order_id>/models/<int:item_id>/add-op/', superadmin_views.superadmin_order_model_add_operation, name='superadmin_order_model_add_operation'),
    path('orders/<int:order_id>/models/<int:item_id>/delete-op/<int:ao_id>/', superadmin_views.superadmin_order_model_delete_operation, name='superadmin_order_model_delete_operation'),
    path('orders/<int:order_id>/delete/', superadmin_views.superadmin_order_delete, name='superadmin_order_delete'),
]

