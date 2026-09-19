from django.urls import path
from . import sticker_views

urlpatterns = [
    path('', sticker_views.sticker_dashboard, name='sticker_dashboard'),
    path('orders/<int:order_id>/', sticker_views.sticker_order_boxes, name='sticker_order_boxes'),
    path('boxes/<int:box_id>/mark-printed/', sticker_views.sticker_mark_box_printed, name='sticker_mark_box_printed'),
    path('orders/<int:order_id>/mark-all-printed/', sticker_views.sticker_mark_all_printed, name='sticker_mark_all_printed'),
]

