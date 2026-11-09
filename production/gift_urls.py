from django.urls import path

from . import gift_views

app_name = 'gifts'

urlpatterns = [
    path('boshqaruv/', gift_views.gifts_manage_view, name='manage'),
    path('api/<str:patok>/version/', gift_views.gifts_version_api, name='version'),
    path('<str:patok>/', gift_views.gifts_tv_view, name='tv'),
]
