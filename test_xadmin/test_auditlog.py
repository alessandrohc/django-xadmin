# coding=utf-8
"""AuditLog: bulk thresholds, object resolution and what ends up in xadmin.models.Log.

Ported verbatim from tests/xtests/auditlog/tests.py (#7369). Only the DummyModel import
changed. The legacy tests/ tree could not run past Django 5.0 -- its runner calls
run_tests(extra_tests=...), removed in 5.0 -- so these 52 tests were the only coverage
of xadmin/auditlog.py (93 statements, used by at least six plugins of the host project)
and nothing was running them: matrix.sh only drives test_xadmin/.
"""
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.test.client import RequestFactory

from xadmin.auditlog import AuditLog, BULK_LOG_THRESHOLD, _resolve_objs, _get_pks
from xadmin.models import Log

from test_xadmin.fixtureapp.models import DummyModel


class AuditLogTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User
        cls.user = User.objects.create_user(username='testuser', password='pass')

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, user=None):
        request = self.factory.get('/fake/')
        request.user = user or self.user
        request.META['REMOTE_ADDR'] = '127.0.0.1'
        return request

    def _create_objs(self, n):
        return [DummyModel.objects.create(name=f'obj-{i}') for i in range(n)]


# ---------------------------------------------------------------------------
# _resolve_objs / _get_pks helpers
# ---------------------------------------------------------------------------
class ResolveObjsTest(AuditLogTestBase):

    def test_queryset_input(self):
        self._create_objs(3)
        qs = DummyModel.objects.all()
        objs, is_queryset, n = _resolve_objs(qs)
        self.assertTrue(is_queryset)
        self.assertEqual(n, 3)

    def test_list_input(self):
        items = self._create_objs(2)
        objs, is_queryset, n = _resolve_objs(items)
        self.assertFalse(is_queryset)
        self.assertEqual(n, 2)

    def test_generator_input(self):
        items = self._create_objs(2)
        gen = (o for o in items)
        objs, is_queryset, n = _resolve_objs(gen)
        self.assertFalse(is_queryset)
        self.assertIsInstance(objs, list)
        self.assertEqual(n, 2)

    def test_empty_queryset(self):
        objs, is_queryset, n = _resolve_objs(DummyModel.objects.none())
        self.assertTrue(is_queryset)
        self.assertEqual(n, 0)

    def test_get_pks_queryset(self):
        items = self._create_objs(3)
        qs = DummyModel.objects.filter(pk__in=[o.pk for o in items])
        pks = _get_pks(qs, is_queryset=True)
        self.assertEqual(sorted(pks), sorted([o.pk for o in items]))

    def test_get_pks_list(self):
        items = self._create_objs(2)
        pks = _get_pks(items, is_queryset=False)
        self.assertEqual(pks, [o.pk for o in items])


# ---------------------------------------------------------------------------
# AuditLog._log
# ---------------------------------------------------------------------------
class LogInternalTest(AuditLogTestBase):

    def test_creates_log_with_obj(self):
        obj = DummyModel.objects.create(name='test')
        request = self._request()
        log = AuditLog._log(request, 'create', obj, 'created it')
        self.assertIsNotNone(log)
        self.assertIsNotNone(log.pk)
        self.assertEqual(log.action_flag, 'create')
        self.assertEqual(log.message, 'created it')
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.ip_addr, '127.0.0.1')
        self.assertIsNotNone(log.content_type)
        self.assertEqual(str(log.object_id), str(obj.pk))

    def test_creates_log_without_obj(self):
        log = AuditLog._log(self._request(), 'update', None, 'no obj')
        self.assertIsNotNone(log.pk)
        self.assertIsNone(log.content_type)
        self.assertIsNone(log.object_id)

    def test_auto_save_false(self):
        obj = DummyModel.objects.create(name='nosave')
        log = AuditLog._log(self._request(), 'create', obj, 'msg', auto_save=False)
        self.assertIsNotNone(log)
        self.assertIsNone(log.pk)

    def test_anonymous_user_returns_none(self):
        request = self._request(user=AnonymousUser())
        log = AuditLog._log(request, 'create', None, 'anon')
        self.assertIsNone(log)

    def test_object_repr_truncated(self):
        long_name = 'x' * 300
        obj = DummyModel.objects.create(name=long_name)
        log = AuditLog._log(self._request(), 'create', obj, '')
        self.assertLessEqual(len(log.object_repr), Log.object_repr_length)

    def test_log_exception_decorator_swallows_errors(self):
        """_log is wrapped with @log_exception — internal errors are logged, not raised."""
        request = self._request()
        with patch('xadmin.auditlog.Log') as MockLog:
            MockLog.side_effect = RuntimeError("boom")
            result = AuditLog._log(request, 'create', None, 'msg')
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# AuditLog.create
# ---------------------------------------------------------------------------
class CreateTest(AuditLogTestBase):

    def test_create_with_message(self):
        obj = DummyModel.objects.create(name='new')
        log = AuditLog.create(self._request(), obj, message='custom msg')
        self.assertEqual(log.action_flag, 'create')
        self.assertEqual(log.message, 'custom msg')

    def test_create_without_message(self):
        obj = DummyModel.objects.create(name='new')
        log = AuditLog.create(self._request(), obj)
        self.assertEqual(log.message, '')


# ---------------------------------------------------------------------------
# AuditLog.update
# ---------------------------------------------------------------------------
class UpdateTest(AuditLogTestBase):

    def test_update_with_explicit_message(self):
        obj = DummyModel.objects.create(name='u')
        log = AuditLog.update(self._request(), obj, fields=['name'], message='override')
        self.assertEqual(log.message, 'override')

    def test_update_auto_generates_message_from_fields(self):
        obj = DummyModel.objects.create(name='u')
        log = AuditLog.update(self._request(), obj, fields=['name', 'email'])
        self.assertIn('name', log.message)
        self.assertIn('email', log.message)

    def test_update_auto_save_false(self):
        obj = DummyModel.objects.create(name='u')
        log = AuditLog.update(self._request(), obj, fields=['name'], auto_save=False)
        self.assertIsNone(log.pk)


# ---------------------------------------------------------------------------
# AuditLog.delete
# ---------------------------------------------------------------------------
class DeleteTest(AuditLogTestBase):

    def test_delete_with_message(self):
        obj = DummyModel.objects.create(name='d')
        log = AuditLog.delete(self._request(), obj, message='removed it')
        self.assertEqual(log.action_flag, 'delete')
        self.assertEqual(log.message, 'removed it')

    def test_delete_without_message(self):
        obj = DummyModel.objects.create(name='d')
        log = AuditLog.delete(self._request(), obj)
        self.assertEqual(log.message, '')


# ---------------------------------------------------------------------------
# AuditLog.bulk_update — n <= BULK_LOG_THRESHOLD
# ---------------------------------------------------------------------------
class BulkUpdateBelowThresholdTest(AuditLogTestBase):

    def test_creates_one_log_per_object(self):
        objs = self._create_objs(3)
        request = self._request()
        logs = AuditLog.bulk_update(request, objs, fields=['name'])
        self.assertEqual(len(logs), 3)
        for log in logs:
            self.assertEqual(log.action_flag, 'update')
            self.assertIsNotNone(log.pk)

    def test_with_queryset(self):
        self._create_objs(3)
        qs = DummyModel.objects.all()
        logs = AuditLog.bulk_update(self._request(), qs, fields=['name'])
        self.assertEqual(len(logs), 3)

    def test_with_caller_message(self):
        objs = self._create_objs(2)
        logs = AuditLog.bulk_update(self._request(), objs, fields=['name'], message='custom')
        for log in logs:
            self.assertEqual(log.message, 'custom')

    def test_without_caller_message_auto_generates(self):
        objs = self._create_objs(2)
        logs = AuditLog.bulk_update(self._request(), objs, fields=['name'])
        for log in logs:
            self.assertIn('name', log.message)

    def test_empty_list(self):
        logs = AuditLog.bulk_update(self._request(), [], fields=['name'])
        self.assertEqual(len(logs), 0)

    def test_post_save_signal_dispatched(self):
        from django.db.models.signals import post_save
        received = []

        def handler(sender, instance, **kwargs):
            received.append(instance)

        post_save.connect(handler, sender=Log)
        try:
            objs = self._create_objs(2)
            AuditLog.bulk_update(self._request(), objs, fields=['name'])
            self.assertEqual(len(received), 2)
        finally:
            post_save.disconnect(handler, sender=Log)

    def test_anonymous_user_produces_empty_logs(self):
        objs = self._create_objs(2)
        request = self._request(user=AnonymousUser())
        logs = AuditLog.bulk_update(request, objs, fields=['name'])
        self.assertEqual(len(logs), 0)


# ---------------------------------------------------------------------------
# AuditLog.bulk_update — n > BULK_LOG_THRESHOLD
# ---------------------------------------------------------------------------
class BulkUpdateAboveThresholdTest(AuditLogTestBase):

    def test_creates_single_summary_log(self):
        objs = self._create_objs(BULK_LOG_THRESHOLD + 1)
        request = self._request()
        log = AuditLog.bulk_update(request, objs, fields=['name'])
        self.assertIsInstance(log, Log)
        self.assertEqual(log.action_flag, 'update')
        self.assertIsNone(log.content_type)
        self.assertIn('name', log.message)

    def test_summary_contains_pks(self):
        objs = self._create_objs(BULK_LOG_THRESHOLD + 1)
        log = AuditLog.bulk_update(self._request(), objs, fields=['name'])
        for obj in objs:
            self.assertIn(str(obj.pk), log.message)

    def test_with_queryset(self):
        self._create_objs(BULK_LOG_THRESHOLD + 1)
        qs = DummyModel.objects.all()
        log = AuditLog.bulk_update(self._request(), qs, fields=['name'])
        self.assertIsInstance(log, Log)
        self.assertEqual(Log.objects.filter(action_flag='update').count(), 1)

    def test_with_caller_message_no_type_error(self):
        """Regression test for MANAGER-2G4: passing message= to bulk_update
        with n > threshold must not raise TypeError."""
        objs = self._create_objs(BULK_LOG_THRESHOLD + 1)
        log = AuditLog.bulk_update(self._request(), objs, message='caller msg')
        self.assertIsInstance(log, Log)
        self.assertIn('multiple fields', log.message)

    def test_fields_none_uses_multiple_fields_label(self):
        objs = self._create_objs(BULK_LOG_THRESHOLD + 1)
        log = AuditLog.bulk_update(self._request(), objs, fields=None)
        self.assertIn('multiple fields', log.message)


# ---------------------------------------------------------------------------
# AuditLog.bulk_delete — n <= BULK_LOG_THRESHOLD
# ---------------------------------------------------------------------------
class BulkDeleteBelowThresholdTest(AuditLogTestBase):

    def test_creates_one_log_per_object(self):
        objs = self._create_objs(3)
        logs = AuditLog.bulk_delete(self._request(), objs)
        self.assertEqual(len(logs), 3)
        for log in logs:
            self.assertEqual(log.action_flag, 'delete')
            self.assertIsNotNone(log.pk)

    def test_with_queryset(self):
        self._create_objs(3)
        qs = DummyModel.objects.all()
        logs = AuditLog.bulk_delete(self._request(), qs)
        self.assertEqual(len(logs), 3)

    def test_empty_list(self):
        logs = AuditLog.bulk_delete(self._request(), [])
        self.assertEqual(len(logs), 0)

    def test_with_message_kwarg_no_error(self):
        """Ensure message= in kwargs is safely consumed and does not propagate."""
        objs = self._create_objs(2)
        logs = AuditLog.bulk_delete(self._request(), objs, message='ignored')
        self.assertEqual(len(logs), 2)
        for log in logs:
            self.assertEqual(log.message, '')

    def test_post_save_signal_dispatched(self):
        from django.db.models.signals import post_save
        received = []

        def handler(sender, instance, **kwargs):
            received.append(instance)

        post_save.connect(handler, sender=Log)
        try:
            objs = self._create_objs(3)
            AuditLog.bulk_delete(self._request(), objs)
            self.assertEqual(len(received), 3)
        finally:
            post_save.disconnect(handler, sender=Log)


# ---------------------------------------------------------------------------
# AuditLog.bulk_delete — n > BULK_LOG_THRESHOLD
# ---------------------------------------------------------------------------
class BulkDeleteAboveThresholdTest(AuditLogTestBase):

    def test_creates_single_summary_log(self):
        objs = self._create_objs(BULK_LOG_THRESHOLD + 1)
        log = AuditLog.bulk_delete(self._request(), objs)
        self.assertIsInstance(log, Log)
        self.assertEqual(log.action_flag, 'delete')
        self.assertIsNone(log.content_type)

    def test_summary_contains_pks(self):
        objs = self._create_objs(BULK_LOG_THRESHOLD + 1)
        log = AuditLog.bulk_delete(self._request(), objs)
        for obj in objs:
            self.assertIn(str(obj.pk), log.message)

    def test_with_queryset(self):
        self._create_objs(BULK_LOG_THRESHOLD + 1)
        qs = DummyModel.objects.all()
        log = AuditLog.bulk_delete(self._request(), qs)
        self.assertIsInstance(log, Log)
        self.assertEqual(Log.objects.filter(action_flag='delete').count(), 1)

    def test_with_message_kwarg_no_type_error(self):
        """Regression test: passing message= to bulk_delete with n > threshold
        must not raise TypeError."""
        objs = self._create_objs(BULK_LOG_THRESHOLD + 1)
        log = AuditLog.bulk_delete(self._request(), objs, message='ignored')
        self.assertIsInstance(log, Log)


# ---------------------------------------------------------------------------
# AuditLog.bulk_update at exact threshold boundary
# ---------------------------------------------------------------------------
class BulkThresholdBoundaryTest(AuditLogTestBase):

    def test_at_threshold_uses_per_object_path(self):
        objs = self._create_objs(BULK_LOG_THRESHOLD)
        logs = AuditLog.bulk_update(self._request(), objs, fields=['name'])
        self.assertEqual(len(logs), BULK_LOG_THRESHOLD)

    def test_one_above_threshold_uses_summary_path(self):
        objs = self._create_objs(BULK_LOG_THRESHOLD + 1)
        result = AuditLog.bulk_update(self._request(), objs, fields=['name'])
        self.assertIsInstance(result, Log)
        self.assertEqual(Log.objects.filter(action_flag='update').count(), 1)


# ---------------------------------------------------------------------------
# Edge cases and integration
# ---------------------------------------------------------------------------
class EdgeCaseTest(AuditLogTestBase):

    def test_bulk_update_generator_input(self):
        items = self._create_objs(3)
        gen = (o for o in items)
        logs = AuditLog.bulk_update(self._request(), gen, fields=['name'])
        self.assertEqual(len(logs), 3)

    def test_bulk_delete_generator_input(self):
        items = self._create_objs(3)
        gen = (o for o in items)
        logs = AuditLog.bulk_delete(self._request(), gen)
        self.assertEqual(len(logs), 3)

    def test_bulk_update_with_message_and_fields(self):
        """When both message and fields are provided, message takes precedence."""
        objs = self._create_objs(2)
        logs = AuditLog.bulk_update(self._request(), objs, fields=['name'], message='priority')
        for log in logs:
            self.assertEqual(log.message, 'priority')

    def test_bulk_update_with_message_none_uses_fields(self):
        """When message is None, auto-generates from fields."""
        objs = self._create_objs(2)
        logs = AuditLog.bulk_update(self._request(), objs, fields=['name'], message=None)
        for log in logs:
            self.assertIn('name', log.message)

    def test_log_records_ip_address(self):
        obj = DummyModel.objects.create(name='ip-test')
        request = self._request()
        request.META['REMOTE_ADDR'] = '192.168.1.1'
        log = AuditLog.create(request, obj)
        self.assertEqual(log.ip_addr, '192.168.1.1')

    def test_log_handles_missing_remote_addr(self):
        obj = DummyModel.objects.create(name='no-ip')
        request = self._request()
        del request.META['REMOTE_ADDR']
        log = AuditLog.create(request, obj)
        self.assertIsNone(log.ip_addr)


# ---------------------------------------------------------------------------
# Log.__str__ — the LogAdmin "data history" column
# ---------------------------------------------------------------------------
class LogStrTest(TestCase):
    """str(Log) must always show the change message when it is filled, not only
    for update/change. Built in memory (no save) — __str__ touches no database."""

    def test_create_with_message_shows_description(self):
        text = str(Log(action_flag='create', object_repr='Foo', message='my description'))
        self.assertIn('Foo', text)
        self.assertIn('my description', text)

    def test_create_without_message_has_no_dangling_separator(self):
        text = str(Log(action_flag='create', object_repr='Foo', message=''))
        self.assertNotIn(' - ', text)

    def test_update_still_shows_message(self):
        text = str(Log(action_flag='update', object_repr='Foo', message='changed name'))
        self.assertIn('changed name', text)

    def test_delete_with_message_appends_description(self):
        text = str(Log(action_flag='delete', object_repr='Foo', message='reason'))
        self.assertIn('reason', text)
