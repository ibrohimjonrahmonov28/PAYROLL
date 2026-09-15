from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('workers/', views.worker_list_view, name='worker_list'),
    path('workers/print-badges/', views.worker_badges_print_view, name='print_worker_badges'),
]

