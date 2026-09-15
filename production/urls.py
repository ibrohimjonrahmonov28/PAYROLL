from django.urls import path
from . import views

app_name = 'production'

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('orders/', views.order_list_view, name='order_list'),
    path('orders/<int:order_id>/', views.order_detail_view, name='order_detail'),
    path('orders/<int:order_id>/print-all/', views.order_print_all_stickers_view, name='order_print_all_stickers'),
    path('boxes/<int:box_id>/split/', views.box_split_wizard_view, name='box_split_wizard'),
    path('boxes/<int:box_id>/print/', views.box_print_stickers_view, name='box_print_stickers'),
    path('articles/', views.articles_catalog_view, name='articles_catalog'),
]
