from django.urls import path

from production import manager_new_views as views

app_name = 'managers_new'

urlpatterns = [
    path('', views.order_list, name='order_list'),
    path('orders/new/', views.order_form, name='order_create'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    path('orders/<int:order_id>/edit/', views.order_form, name='order_edit'),
    path('orders/<int:order_id>/status/', views.order_status, name='order_status'),
    path('orders/<int:order_id>/manager/', views.order_reassign, name='order_reassign'),
    path('orders/<int:order_id>/delete/', views.order_delete, name='order_delete'),
    path('orders/<int:order_id>/articles/new/', views.article_form, name='article_create'),
    path('orders/<int:order_id>/articles/<int:item_id>/', views.article_form, name='article_edit'),
    path('orders/<int:order_id>/articles/<int:item_id>/delete/', views.article_delete, name='article_delete'),
    path('orders/<int:order_id>/excel/order-list/', views.export_order_list, name='export_order_list'),
    path('orders/<int:order_id>/excel/fabric-order/', views.export_fabric_order, name='export_fabric_order'),
]
