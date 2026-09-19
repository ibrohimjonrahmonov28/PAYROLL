from django.urls import path
from . import norma_views

urlpatterns = [
    path('', norma_views.norma_dashboard, name='norma_dashboard'),
    path('models/', norma_views.norma_models_list, name='norma_models_list'),
    path('models/create/', norma_views.norma_model_create, name='norma_model_create'),
    path('models/<int:model_id>/edit/', norma_views.norma_model_edit, name='norma_model_edit'),
    path('models/<int:model_id>/assign-articles/', norma_views.norma_assign_articles, name='norma_assign_articles'),
    path('models/<int:model_id>/operations/', norma_views.norma_model_operations, name='norma_model_operations'),
    path('models/<int:model_id>/operations/add/', norma_views.norma_model_add_operation, name='norma_model_add_operation'),
    path('models/<int:model_id>/operations/bulk-update/', norma_views.norma_model_update_operations, name='norma_model_update_operations'),
    path('models/<int:model_id>/operations/<int:mo_id>/delete/', norma_views.norma_model_delete_operation, name='norma_model_delete_operation'),
    path('operations/', norma_views.norma_operations_catalog, name='norma_operations_catalog'),
    path('history/', norma_views.norma_history, name='norma_history'),
    path('workers/', norma_views.norma_workers, name='norma_workers'),
    path('sync/', norma_views.norma_sync, name='norma_sync'),
]

