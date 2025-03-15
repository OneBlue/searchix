from . import models
from django.urls import path, re_path, include
from django.contrib import admin
from django.contrib.staticfiles import views
from .views import attachment

urlpatterns = [
    path('download/attachment/<int:id>/', attachment.attachment_download),
    path('', admin.site.urls),
]
