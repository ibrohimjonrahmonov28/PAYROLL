from django.urls import path

from . import meto_flow_views as flow
from . import tayyorlov_stiker_views as tstiker
from .meto_container_views import (
    container_create_view,
    container_detail_view,
    container_labels_view,
    containers_view,
)

# Tayyorlov bo'limi (/tayyorlov/): Bosh bino (Meto → Tasnif → Stiker → Tayyorlov skladi), Filial (Uychi) va Konteynerlar
urlpatterns = [
    path('', flow.flow_view, name='meto_hub_flow'),
    path('meto/', flow.section_view, {'tab': 'meto'}, name='meto_hub_flow_meto'),
    path('tasnif/', flow.section_view, {'tab': 'tasnif'}, name='meto_hub_flow_tasnif'),
    path('stiker/', flow.section_view, {'tab': 'stiker'}, name='meto_hub_flow_stiker'),
    # Tayyorlov stiker chiqarish (/stickers/ ning 1 ga 1 nusxasi, Tasnif soni bilan)
    path('stiker/orders/<int:order_id>/', tstiker.tayyorlov_stiker_order_boxes, name='tayyorlov_stiker_order_boxes'),
    path('stiker/orders/<int:order_id>/batches/<int:batch_id>/boxes/', tstiker.tayyorlov_stiker_batch_boxes, name='tayyorlov_stiker_batch_boxes'),
    path('stiker/orders/<int:order_id>/batches/<int:batch_id>/mark-printed/', tstiker.tayyorlov_stiker_mark_batch_printed, name='tayyorlov_stiker_mark_batch_printed'),
    path('stiker/orders/<int:order_id>/regenerate-tickets/', tstiker.tayyorlov_stiker_regenerate_tickets, name='tayyorlov_stiker_regenerate_tickets'),
    path('stiker/orders/<int:order_id>/copy-operations/', tstiker.tayyorlov_stiker_copy_operations, name='tayyorlov_stiker_copy_operations'),
    path('stiker/orders/<int:order_id>/mark-all-printed/', tstiker.tayyorlov_stiker_mark_all_printed, name='tayyorlov_stiker_mark_all_printed'),
    path('stiker/boxes/<int:box_id>/mark-printed/', tstiker.tayyorlov_stiker_mark_box_printed, name='tayyorlov_stiker_mark_box_printed'),
    path('sklad/', flow.section_view, {'tab': 'sklad'}, name='meto_hub_flow_sklad'),
    path('uychi/', flow.section_view, {'tab': 'uychi'}, name='meto_hub_flow_uychi'),
    path('yukxat/', flow.section_view, {'tab': 'yukxat'}, name='meto_hub_flow_yukxat'),
    path('zakazlar/', flow.section_view, {'tab': 'zakazlar'}, name='meto_hub_flow_zakazlar'),
    path('<int:batch_id>/meto/qabul/', flow.meto_take, name='meto_hub_flow_meto_take'),
    path('<int:batch_id>/meto/bekor/', flow.meto_cancel, name='meto_hub_flow_meto_cancel'),
    path('<int:batch_id>/meto/tugatish/', flow.meto_finish, name='meto_hub_flow_meto_finish'),
    path('<int:batch_id>/tasnif/qabul/', flow.tasnif_take, name='meto_hub_flow_tasnif_take'),
    path('<int:batch_id>/tasnif/bekor/', flow.tasnif_cancel, name='meto_hub_flow_tasnif_cancel'),
    path('<int:batch_id>/tasnif/qaytarish/', flow.tasnif_return, name='meto_hub_flow_tasnif_return'),
    path('<int:batch_id>/tasnif/tugatish/', flow.tasnif_finish, name='meto_hub_flow_tasnif_finish'),
    path('<int:batch_id>/tasnif/ozgartirish/', flow.tasnif_update, name='meto_hub_flow_tasnif_update'),
    path('<int:batch_id>/qutilar/', flow.split, name='meto_hub_flow_split'),
    path('<int:batch_id>/sklad/qabul/', flow.sklad_receive, name='meto_hub_flow_sklad_receive'),
    path('zakaz/<int:order_id>/beta/', flow.beta_toggle, name='meto_hub_flow_beta_toggle'),
    path('api/resolve/', flow.api_resolve, name='meto_hub_flow_api_resolve'),
    path('api/link/', flow.api_link, name='meto_hub_flow_api_link'),
    path('api/unlink/', flow.api_unlink, name='meto_hub_flow_api_unlink'),
    path('api/check/', flow.api_check, name='meto_hub_flow_api_check'),
    path('api/give/', flow.api_give, name='meto_hub_flow_api_give'),
    path('api/ship/', flow.api_ship, name='meto_hub_flow_api_ship'),
    path('api/receive/<int:line_id>/', flow.api_receive, name='meto_hub_flow_api_receive'),
    path('api/return/<int:line_id>/', flow.api_return, name='meto_hub_flow_api_return'),
    path('yukxat/<int:shipment_id>/chop-etish/', flow.shipment_print, name='meto_hub_shipment_print'),
    # Konteynerlar (/tayyorlov/konteynerlar/)
    path('konteynerlar/', containers_view, name='meto_hub_containers'),
    path('konteynerlar/yaratish/', container_create_view, name='meto_hub_container_create'),
    path('konteynerlar/yorliq/', container_labels_view, name='meto_hub_container_labels'),
    path('konteynerlar/<int:number>/', container_detail_view, name='meto_hub_container_detail'),
]

