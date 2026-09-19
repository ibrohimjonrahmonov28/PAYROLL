from django.urls import path
from . import cutting_views

urlpatterns = [
    path('', cutting_views.cutting_dashboard, name='cutting_dashboard'),
    path('orders/<int:order_id>/', cutting_views.cutting_order_detail, name='cutting_order_detail'),
    path('orders/<int:order_id>/items/<int:order_item_id>/add-batch/', cutting_views.cutting_add_batch, name='cutting_add_batch'),
    path('batches/<int:batch_id>/split-boxes/', cutting_views.cutting_split_and_create_boxes, name='cutting_split_and_create_boxes'),
]

