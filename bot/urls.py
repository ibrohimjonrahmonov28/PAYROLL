from django.urls import path
from . import views

app_name = 'bot'

urlpatterns = [
    path('simulator/', views.bot_simulator_view, name='bot_simulator'),
    path('simulator/api/', views.simulator_action_api, name='simulator_api'),
]
