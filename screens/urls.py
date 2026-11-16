from django.urls import path
from . import views

app_name = 'screens'

urlpatterns = [
    # Taxlovchilar TV ekranlari (2026-11-14) — umumiy <str:screen_identifier> dan oldin
    path('taxlov/sozlash/', views.packer_screens_settings, name='packer_settings'),
    path('taxlov/api/<int:number>/', views.packer_screen_api, name='packer_api'),
    path('taxlov/<int:number>/', views.packer_screen_view, name='packer_view'),
    path('', views.all_screens_overview, name='overview'),
    path('', views.all_screens_overview, name='all_screens_overview'),
    path('overview/', views.all_screens_overview, name='overview_alias'),
    path('<int:screen_number>/', views.screen_view, name='screen_view'),
    path('<str:screen_identifier>/', views.screen_view, name='screen_view_code'),
    path('api/<int:screen_number>/', views.screen_api_view, name='screen_api'),
    path('api/<str:screen_identifier>/', views.screen_api_view, name='screen_api_code'),
]
