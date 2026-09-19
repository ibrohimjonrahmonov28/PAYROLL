from django.urls import path
from . import meto_views

urlpatterns = [
    path('', meto_views.meto_dashboard, name='meto_dashboard'),
    path('orders/<int:order_id>/', meto_views.meto_order_detail, name='meto_order_detail'),
    path('items/<int:item_id>/confirm/', meto_views.meto_confirm_item, name='meto_confirm_item'),
]

