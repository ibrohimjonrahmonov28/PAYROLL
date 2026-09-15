from django.urls import path
from . import superadmin_views

urlpatterns = [
    path('', superadmin_views.superadmin_dashboard, name='superadmin_dashboard'),
    path('users/', superadmin_views.superadmin_users, name='superadmin_users'),
    path('payroll/', superadmin_views.superadmin_payroll, name='superadmin_payroll'),
    path('payroll/payout/', superadmin_views.superadmin_payout_create, name='superadmin_payout_create'),
    path('payroll/export/', superadmin_views.superadmin_payroll_export_csv, name='superadmin_payroll_export'),
    path('pricing/', superadmin_views.superadmin_pricing, name='superadmin_pricing'),
    path('orders/', superadmin_views.superadmin_orders_list, name='superadmin_orders_list'),
    path('orders/<int:order_id>/', superadmin_views.superadmin_order_detail, name='superadmin_order_detail'),
    path('orders/<int:order_id>/add-model/', superadmin_views.superadmin_order_add_model, name='superadmin_order_add_model'),
]

