from django.urls import path
from . import views

app_name = 'screens'

urlpatterns = [
    path('', views.all_screens_overview, name='overview'),
    path('<int:screen_number>/', views.screen_view, name='screen_view'),
    path('<str:screen_identifier>/', views.screen_view, name='screen_view_code'),
    path('api/<int:screen_number>/', views.screen_api_view, name='screen_api'),
    path('api/<str:screen_identifier>/', views.screen_api_view, name='screen_api_code'),
]

