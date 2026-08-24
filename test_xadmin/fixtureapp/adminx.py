# coding=utf-8
"""Registered through xadmin's normal autodiscover, exactly as a real app would be.

``reversion_enable = True`` is the flag ``xversion.register_models()`` looks for, and
``inlines`` is a plain list rather than a property on purpose -- register_models()
deliberately skips admins whose ``inlines`` is a property, so a property here would make
the positive test pass for the wrong reason.
"""
from xadmin.sites import site
from xadmin.views import BaseAdminPlugin  # noqa: F401  (keeps import order honest)

from test_xadmin.fixtureapp.models import Author, Book


class BookInline:
    model = Book
    extra = 1
    style = 'tab'


class AuthorAdmin:
    list_display = ('name',)
    reversion_enable = True
    inlines = [BookInline]


site.register(Author, AuthorAdmin)
site.register(Book)
