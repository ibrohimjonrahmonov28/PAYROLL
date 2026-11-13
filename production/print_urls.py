from django.urls import path

from production import print_views as views

app_name = 'printers'

urlpatterns = [
    path('', views.printers_page, name='page'),
    path('<int:screen_number>/test/', views.printer_test, name='test'),
    path('jobs/<int:job_id>/reprint/', views.job_reprint, name='reprint'),
    path('agent/jobs/', views.agent_jobs, name='agent_jobs'),
    path('agent/jobs/<int:job_id>/result/', views.agent_result, name='agent_result'),
]
