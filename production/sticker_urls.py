from django.urls import path
from . import sticker_views

urlpatterns = [
    path('', sticker_views.sticker_dashboard, name='sticker_dashboard'),
    path('orders/<int:order_id>/', sticker_views.sticker_order_boxes, name='sticker_order_boxes'),
    path('orders/<int:order_id>/batches/<int:batch_id>/boxes/', sticker_views.sticker_batch_boxes_view, name='sticker_batch_boxes'),
    path('orders/<int:order_id>/batches/<int:batch_id>/mark-printed/', sticker_views.sticker_mark_batch_printed, name='sticker_mark_batch_printed'),
    path('orders/<int:order_id>/unassigned-boxes/', sticker_views.sticker_unassigned_boxes_view, name='sticker_unassigned_boxes'),
    path('orders/<int:order_id>/regenerate-tickets/', sticker_views.sticker_regenerate_tickets, name='sticker_regenerate_tickets'),
    path('orders/<int:order_id>/copy-operations/', sticker_views.sticker_copy_operations_and_generate, name='sticker_copy_operations_and_generate'),
    path('boxes/<int:box_id>/mark-printed/', sticker_views.sticker_mark_box_printed, name='sticker_mark_box_printed'),
    path('orders/<int:order_id>/mark-all-printed/', sticker_views.sticker_mark_all_printed, name='sticker_mark_all_printed'),
]
