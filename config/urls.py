"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from accounts.views import login_view, logout_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', login_view, name='root_login'),
    path('logout/', logout_view, name='root_logout'),
    path('', include('production.urls')),
    path('superadmin/', include('accounts.superadmin_urls')),
    path('managers/', include('production.manager_urls')),
    path('cutting/', include('production.cutting_urls')),
    path('meto/', include('production.meto_urls')),
    path('stickers/', include('production.sticker_urls')),
    path('accounts/', include('accounts.urls')),
    path('screens/', include('screens.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

