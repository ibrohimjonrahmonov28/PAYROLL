from django.urls import path
from . import cutting_views

urlpatterns = [
    path('', cutting_views.cutting_dashboard, name='cutting_dashboard'),
    path('orders/<int:order_id>/', cutting_views.cutting_order_detail, name='cutting_order_detail'),
    path('orders/<int:order_id>/items/<int:order_item_id>/detail/', cutting_views.cutting_item_detail_view, name='cutting_item_detail'),
    path('orders/<int:order_id>/items/<int:order_item_id>/add-batch/', cutting_views.cutting_add_batch, name='cutting_add_batch'),
    path('orders/<int:order_id>/batches/<int:batch_id>/edit/', cutting_views.cutting_edit_batch, name='cutting_edit_batch'),
    path('orders/<int:order_id>/batches/<int:batch_id>/delete/', cutting_views.cutting_delete_batch, name='cutting_delete_batch'),
    path('api/check-pastal/', cutting_views.api_check_pastal_code, name='api_cutting_check_pastal'),
    path('api/next-pastal/', cutting_views.api_get_next_pastal_code, name='api_cutting_next_pastal'),
]

