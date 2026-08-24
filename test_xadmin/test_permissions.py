# coding=utf-8
"""The post_migrate hook survives a backend that cannot ignore conflicts. See #7205.

``add_view_permissions`` called ``bulk_create(..., ignore_conflicts=True)``
unconditionally. ``mssql-django`` declares ``supports_ignore_conflicts = False``, so
Django raises ``NotSupportedError`` *before* touching the database, and ``manage.py
migrate`` died on every SQL Server install after applying its migrations.

The damage is larger than one traceback. In Django 4.2 ``Signal.send()`` is a list
comprehension, so the first receiver that raises kills the whole chain -- and this
receiver is connected at module import (``xadmin/models.py``), while Django connects
``create_permissions`` and ``create_contenttypes`` from ``AppConfig.ready()``, a later
phase. Measured receiver order on post_migrate:

    0. add_view_permissions   (xadmin.models)
    1. create_permissions     (django.contrib.auth.management)
    2. create_contenttypes    (django.contrib.contenttypes.management)

So xadmin's hook fires FIRST and takes contenttypes and permissions down with it: new
models end up with no ``django_content_type`` row and no ``auth_permission`` rows, and
every plugin's own post_migrate receiver is skipped.

The backend cannot be swapped in this suite -- dev runs MySQL and the target is SQL
Server -- so the capability flag is patched instead. That is the exact predicate the
production failure turned on.
"""
from unittest import mock

from django.db import DEFAULT_DB_ALIAS
from django.db.utils import NotSupportedError
from django.test import SimpleTestCase

from xadmin.models import add_view_permissions


class BulkCreateCapabilityTests(SimpleTestCase):
    """The hook must ask the backend before using ignore_conflicts."""

    def _run_with(self, supports, using=DEFAULT_DB_ALIAS):
        """Call the receiver against a fake backend and capture the bulk_create call."""
        captured = {}
        fake_ct = mock.Mock(pk=1, model='thing', name='thing')

        class FakeQuerySet:
            """Answers the whole chain the hook uses: filter/values_list/all/bulk_create."""

            def filter(self, **kwargs):
                return self

            def values_list(self, *fields):
                return []

            def all(self):
                return [fake_ct]

            def bulk_create(self, objs, **kwargs):
                captured['objs'] = objs
                captured['kwargs'] = kwargs
                # Reproduce Django's own guard: it refuses before hitting the database.
                if kwargs.get('ignore_conflicts') and not supports:
                    raise NotSupportedError(
                        'This database backend does not support ignoring conflicts.')
                return objs

        class FakeManager:
            """`using()` records the alias -- that is half of what #7205 is about."""

            def using(self, alias):
                captured.setdefault('using', alias)
                return FakeQuerySet()

            # No bare bulk_create/filter here on purpose: if the hook ever stops going
            # through using(), it fails loudly instead of silently hitting `default`.

        with mock.patch('xadmin.models.Permission') as permission, \
                mock.patch('xadmin.models.ContentType') as content_type:
            permission.objects = FakeManager()
            permission.side_effect = lambda **kw: mock.Mock(**kw)
            content_type.objects.using.return_value = FakeQuerySet()

            connection = mock.Mock(
                features=mock.Mock(supports_ignore_conflicts=supports))
            fake_connections = {using: connection, DEFAULT_DB_ALIAS: connection}
            with mock.patch('xadmin.models.connections', new=fake_connections):
                add_view_permissions(sender=None, using=using)
        return captured

    def test_ignore_conflicts_is_off_when_the_backend_cannot_do_it(self):
        """The #7205 regression, stated as a contract.

        On mssql-django this is the difference between a completed migrate and a
        half-migrated database with no content types.
        """
        captured = self._run_with(supports=False)
        self.assertIn('kwargs', captured, msg='bulk_create was never called')
        self.assertFalse(
            captured['kwargs'].get('ignore_conflicts'),
            msg='ignore_conflicts must be gated on '
                'connections[db].features.supports_ignore_conflicts')

    def test_ignore_conflicts_is_used_where_it_is_supported(self):
        """No pointless behaviour change on MySQL/Postgres, which do support it."""
        captured = self._run_with(supports=True)
        self.assertTrue(captured['kwargs'].get('ignore_conflicts'))

    def test_the_receiver_does_not_raise_on_an_unsupporting_backend(self):
        """The symptom itself: migrate must not die in emit_post_migrate_signal."""
        try:
            self._run_with(supports=False)
        except NotSupportedError as exc:
            self.fail('add_view_permissions still raises on a backend without '
                      'ignore_conflicts support: {0}'.format(exc))


class DatabaseAliasTests(SimpleTestCase):
    """The hook must write to the database post_migrate is talking about."""

    def test_the_using_alias_from_the_signal_is_honoured(self):
        """post_migrate passes `using`; ignoring it writes to the wrong database.

        On a multi-database install the hook would create permissions on `default`
        while the migration ran somewhere else -- silently, with no error.
        """
        captured = BulkCreateCapabilityTests()._run_with(supports=True, using='other')
        self.assertEqual(
            captured.get('using'), 'other',
            msg='add_view_permissions must honour the using alias post_migrate hands it')
