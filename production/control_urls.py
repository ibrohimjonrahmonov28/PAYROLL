from django.urls import path
from . import control_v2_views, control_views

app_name = 'control'

urlpatterns = [
    path('', control_views.control_home_view, name='home'),
    path('api/lookup/', control_views.control_box_lookup_api, name='api_lookup'),
    path('api/submit/', control_views.control_submit_inspection_api, name='api_submit'),
    path('api/recent/', control_views.control_recent_inspections_api, name='api_recent'),
    path('api/identify-operator/', control_views.control_identify_operator_api, name='api_identify_operator'),
    path('api/logout-operator/', control_views.control_logout_operator_api, name='api_logout_operator'),
    path('download-apk/', control_views.control_download_apk_view, name='download_apk'),
    # OTK v2 (beta, 2026-11-19): /control/ nusxasi + yangi meto oqimi konteynerlari; /control/ o'zgarmaydi
    path('v2/', control_v2_views.control_v2_home_view, name='v2_home'),
    path('v2/api/lookup/', control_v2_views.control_v2_lookup_api, name='v2_api_lookup'),
    path('v2/api/submit/', control_v2_views.control_v2_submit_api, name='v2_api_submit'),
    path('v2/api/container/', control_v2_views.control_v2_container_check_api, name='v2_api_container'),
]

