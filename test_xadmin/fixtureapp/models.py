# coding=utf-8
"""Two models with a plain FK, so an inline admin has a reverse relation to walk."""
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)
    # Deliberately NOT in AuthorAdmin.list_display: this is the stand-in for
    # User.password in the #7369 leak. If it ever reaches a response, the
    # ?_fields= filter is not doing its job.
    secret = models.CharField(max_length=100, default='top-secret-value')
    # Two booleans so the list filter can be exercised on both sides of nullability:
    # only the nullable one should offer the "Unknown" choice.
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(null=True, default=None)

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.name


class Book(models.Model):
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name='books')
    title = models.CharField(max_length=100)

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.title


class DummyModel(models.Model):
    """Plain model for the auditlog suite, ported from tests/xtests/auditlog.

    AuditLog logs against whatever object it is handed; this exists so those tests
    have something to log without dragging the admin registration of Author into it.
    """
    name = models.CharField(max_length=64)

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.name
