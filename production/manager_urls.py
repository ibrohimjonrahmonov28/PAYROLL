from django.urls import path
from . import manager_views

urlpatterns = [
    path('', manager_views.manager_dashboard, name='manager_dashboard'),
    path('orders/new/', manager_views.manager_order_create, name='manager_order_create'),
    path('orders/<int:order_id>/', manager_views.manager_order_detail, name='manager_order_detail'),
    path('orders/<int:order_id>/status/', manager_views.manager_order_update_status, name='manager_order_update_status'),
    path('orders/<int:order_id>/delete/', manager_views.manager_order_delete, name='manager_order_delete'),
    path('orders/<int:order_id>/add-article/', manager_views.manager_order_add_article, name='manager_order_add_article'),
    path('orders/<int:order_id>/items/<int:item_id>/delete/', manager_views.manager_order_delete_item, name='manager_order_delete_item'),
]

