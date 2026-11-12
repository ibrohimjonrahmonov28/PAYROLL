from django.urls import path

from . import packing_views

app_name = 'packing'

# Beta (egasi talabi 2026-11-12): bitta /beta/ sahifasi — ?tab=otk|taxlovchi|sanoqchi|upakovka|zakazlar
urlpatterns = [
    path('', packing_views.hub_view, name='hub'),
    path('tamir-cheki/<int:receipt_id>/', packing_views.repair_receipt_view, name='repair_receipt'),
    path('api/otk/lookup/', packing_views.api_otk_lookup, name='api_otk_lookup'),
    path('api/otk/submit/', packing_views.api_otk_submit, name='api_otk_submit'),
    path('api/identify/', packing_views.api_identify, name='api_identify'),
    path('api/scan/', packing_views.api_scan, name='api_scan'),
    path('sanoqchi/qabul/', packing_views.counter_accept, name='counter_accept'),
    path('sanoqchi/qaytarish/', packing_views.counter_basket_remove, name='counter_remove'),
    path('sanoqchi/jonatish/', packing_views.counter_send, name='counter_send'),
    path('sanoqchi/bekor/', packing_views.counter_cancel, name='counter_cancel'),
    path('zakazlar/almashtirish/', packing_views.beta_toggle, name='beta_toggle'),
]
