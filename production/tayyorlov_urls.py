from django.urls import path

from . import meto_flow_views as flow
from .meto_container_views import (
    container_create_view,
    container_detail_view,
    container_labels_view,
    containers_view,
)

# Tayyorlov bo'limi (/tayyorlov/): Bosh bino (Meto → Tasnif → Stiker → Tayyorlov skladi), Filial (Uychi) va Konteynerlar
urlpatterns = [
    path('', flow.flow_view, name='meto_hub_flow'),
    path('<int:batch_id>/meto/qabul/', flow.meto_take, name='meto_hub_flow_meto_take'),
    path('<int:batch_id>/meto/bekor/', flow.meto_cancel, name='meto_hub_flow_meto_cancel'),
    path('<int:batch_id>/meto/tugatish/', flow.meto_finish, name='meto_hub_flow_meto_finish'),
    path('<int:batch_id>/tasnif/qabul/', flow.tasnif_take, name='meto_hub_flow_tasnif_take'),
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

