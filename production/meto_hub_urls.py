from django.urls import path
from .meto_hub_views import (
    meto_hub_dashboard_view,
    meto_hub_cart_add_view,
    meto_hub_cart_remove_view,
    meto_hub_cart_clear_view,
    meto_hub_dispatch_koson_view,
    meto_hub_waybill_create_view,
    meto_hub_waybill_detail_view,
    meto_hub_waybill_print_view,
    meto_hub_waybill_accept_view,
    meto_hub_waybill_reject_view,
    meto_hub_uychi_view,
    meto_hub_dispatch_uychi_view,
)

urlpatterns = [
    path('', meto_hub_dashboard_view, name='meto_hub_dashboard'),
    path('cart/add/<int:batch_id>/', meto_hub_cart_add_view, name='meto_hub_cart_add'),
    path('cart/remove/<int:batch_id>/', meto_hub_cart_remove_view, name='meto_hub_cart_remove'),
    path('cart/clear/', meto_hub_cart_clear_view, name='meto_hub_cart_clear'),
    path('dispatch/koson/<int:batch_id>/', meto_hub_dispatch_koson_view, name='meto_hub_dispatch_koson'),
    path('waybill/create/', meto_hub_waybill_create_view, name='meto_hub_waybill_create'),
    path('waybill/<int:waybill_id>/', meto_hub_waybill_detail_view, name='meto_hub_waybill_detail'),
    path('waybill/<int:waybill_id>/print/', meto_hub_waybill_print_view, name='meto_hub_waybill_print'),
    path('waybill/<int:waybill_id>/accept/', meto_hub_waybill_accept_view, name='meto_hub_waybill_accept'),
    path('waybill/<int:waybill_id>/reject/', meto_hub_waybill_reject_view, name='meto_hub_waybill_reject'),
    path('uychi/', meto_hub_uychi_view, name='meto_hub_uychi'),
    path('uychi/dispatch/<int:batch_id>/', meto_hub_dispatch_uychi_view, name='meto_hub_dispatch_uychi'),
]
