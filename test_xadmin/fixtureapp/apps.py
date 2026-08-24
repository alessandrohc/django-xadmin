# coding=utf-8
from django.apps import AppConfig


class FixtureAppConfig(AppConfig):
    name = 'test_xadmin.fixtureapp'
    label = 'xadmin_fixture'
    default_auto_field = 'django.db.models.BigAutoField'
