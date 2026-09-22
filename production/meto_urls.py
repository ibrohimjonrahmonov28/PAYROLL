from django.urls import path
from . import meto_views

urlpatterns = [
    path('', meto_views.meto_dashboard, name='meto_dashboard'),
    path('orders/<int:order_id>/', meto_views.meto_order_detail, name='meto_order_detail'),
    path('batches/<int:batch_id>/items/', meto_views.meto_batch_items_view, name='meto_batch_items'),
    path('items/<int:item_id>/confirm/', meto_views.meto_confirm_item, name='meto_confirm_item'),
    path('items/<int:item_id>/reset/', meto_views.meto_reset_item, name='meto_reset_item'),
]

