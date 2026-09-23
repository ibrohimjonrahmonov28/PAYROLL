from django.urls import path
from . import control_views

app_name = 'control'

urlpatterns = [
    path('', control_views.control_home_view, name='home'),
    path('api/lookup/', control_views.control_box_lookup_api, name='api_lookup'),
    path('api/submit/', control_views.control_submit_inspection_api, name='api_submit'),
    path('api/recent/', control_views.control_recent_inspections_api, name='api_recent'),
]

