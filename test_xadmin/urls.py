# coding=utf-8
"""Mounts the admin site.

``site.urls`` is a 3-tuple (patterns, name, app_name), so it goes straight into
``path()`` -- wrapping it in ``include()`` raises ImproperlyConfigured.
"""
from django.urls import path

from xadmin.sites import site

urlpatterns = [
    path('xadmin/', site.urls),
]
