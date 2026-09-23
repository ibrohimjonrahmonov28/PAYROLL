from django.urls import path
from . import cutting_views

urlpatterns = [
    path('', cutting_views.cutting_dashboard, name='cutting_dashboard'),
    path('orders/<int:order_id>/', cutting_views.cutting_order_detail, name='cutting_order_detail'),
    path('orders/<int:order_id>/items/<int:order_item_id>/add-batch/', cutting_views.cutting_add_batch, name='cutting_add_batch'),
    path('orders/<int:order_id>/batches/<int:batch_id>/edit/', cutting_views.cutting_edit_batch, name='cutting_edit_batch'),
    path('orders/<int:order_id>/batches/<int:batch_id>/delete/', cutting_views.cutting_delete_batch, name='cutting_delete_batch'),
]

