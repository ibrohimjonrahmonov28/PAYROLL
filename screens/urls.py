from django.urls import path
from . import views

app_name = 'screens'

urlpatterns = [
    path('', views.all_screens_overview, name='overview'),
    path('<int:screen_number>/', views.screen_view, name='screen_view'),
    path('api/<int:screen_number>/', views.screen_api_view, name='screen_api'),
]
