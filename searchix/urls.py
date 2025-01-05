from . import models
from django.urls import path, re_path, include
from django.contrib import admin
from django.contrib.staticfiles import views

urlpatterns = [
    path('', admin.site.urls),
]
