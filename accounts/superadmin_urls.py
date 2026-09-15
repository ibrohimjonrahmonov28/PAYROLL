from django.urls import path
from . import superadmin_views

urlpatterns = [
    path('', superadmin_views.superadmin_dashboard, name='superadmin_dashboard'),
    path('users/', superadmin_views.superadmin_users, name='superadmin_users'),
    path('payroll/', superadmin_views.superadmin_payroll, name='superadmin_payroll'),
    path('payroll/payout/', superadmin_views.superadmin_payout_create, name='superadmin_payout_create'),
    path('payroll/export/', superadmin_views.superadmin_payroll_export_csv, name='superadmin_payroll_export'),
    path('pricing/', superadmin_views.superadmin_pricing, name='superadmin_pricing'),
]
