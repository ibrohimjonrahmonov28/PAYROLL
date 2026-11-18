from django.urls import path

from . import packing_views

app_name = 'packing'

# Beta (egasi talabi 2026-11-12): bitta /beta/ sahifasi — ?tab=otk|sanoqchi|upakovka|zakazlar
urlpatterns = [
    path('', packing_views.hub_view, name='hub'),
    path('tamir-cheki/<int:receipt_id>/', packing_views.repair_receipt_view, name='repair_receipt'),
    path('api/otk/lookup/', packing_views.api_otk_lookup, name='api_otk_lookup'),
    path('api/otk/submit/', packing_views.api_otk_submit, name='api_otk_submit'),
    path('api/sanoqchi/taxlovchi/', packing_views.api_counter_packer, name='api_counter_packer'),
    path('api/sanoqchi/patok/', packing_views.api_counter_patok, name='api_counter_patok'),
    path('api/sanoqchi/qogoz/', packing_views.api_counter_paper, name='api_counter_paper'),
    path('api/sanoqchi/olib-tashlash/', packing_views.api_counter_remove, name='api_counter_remove'),
    path('api/sanoqchi/yuborish/', packing_views.api_counter_send, name='api_counter_send'),
    path('sanoqchi/qabul/', packing_views.counter_accept, name='counter_accept'),
    path('sanoqchi/qaytarish/', packing_views.counter_basket_remove, name='counter_remove'),
    path('sanoqchi/jonatish/', packing_views.counter_send, name='counter_send'),
    path('sanoqchi/bekor/', packing_views.counter_cancel, name='counter_cancel'),
    path('upakovka/qabul/', packing_views.master_accept, name='master_accept'),
    path('upakovka/qaytarish/', packing_views.master_return, name='master_return'),
    path('api/upakovka/2-sort/jonatish/', packing_views.api_second_sort_dispatch, name='api_second_sort_dispatch'),
    path('upakovka/2-sort/bekor/', packing_views.second_sort_dispatch_cancel, name='second_sort_dispatch_cancel'),
    path('upakovka/2-sort/turlar/qoshish/', packing_views.second_sort_type_create, name='second_sort_type_create'),
    path('upakovka/2-sort/turlar/tahrir/', packing_views.second_sort_type_update, name='second_sort_type_update'),
    path('upakovka/2-sort/hisobot-csv/', packing_views.second_sort_report_csv, name='second_sort_report_csv'),
    path('zakazlar/almashtirish/', packing_views.beta_toggle, name='beta_toggle'),
]
