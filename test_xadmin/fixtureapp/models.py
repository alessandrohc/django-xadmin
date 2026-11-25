# coding=utf-8
"""Two models with a plain FK, so an inline admin has a reverse relation to walk."""
import uuid

from django.conf import settings
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


class Ranked(models.Model):
    """Integer key with an order column: what the ``save-order`` route and the batch action need (#8930).

    ``RankedAdmin.list_order_field`` turns the sortable list on; the ``mark_seen`` action
    renames the selected rows, so a test can tell whether the action ran and on which rows.
    """
    name = models.CharField(max_length=100)
    position = models.IntegerField(default=0)

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.name


class Tag(models.Model):
    """UUID key: the batch action must keep accepting a valid UUID selection (#8930)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.name


class RankedUuid(models.Model):
    """UUID key with an order column: the sortable list of a model whose key is not an integer (#8986).

    ``Ranked`` covers the integer key and ``Tag`` the UUID key without sorting; this is the
    one that has both, so the ``save-order`` route can be exercised with UUID ids.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    position = models.IntegerField(default=0)

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.name


class Note(models.Model):
    """Integer key with an owner: the admin only shows each user the rows they own (#8985).

    ``NoteAdmin.user_can_access_owned_objects_only`` narrows the admin's ``queryset()`` to
    ``owner=<the user>``, so a row can exist and still not be one the admin shows to a given
    user: what the selection of the export must never reach.
    """
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')

    class Meta:
        app_label = 'xadmin_fixture'

    def __str__(self):
        return self.name
