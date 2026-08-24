import functools
import logging

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from django.utils.encoding import force_str
from django.utils.text import Truncator, get_text_list
from django.utils.translation import gettext as _, gettext_lazy as _lazy

from xadmin.models import Log

logger = logging.getLogger('xadmin.auditlog')

# threshold for bulk operations: at or below → one log per object;
# above → single summary log with a list of PKs in the message field
BULK_LOG_THRESHOLD = 50


def _get_content_type_for_model(obj):
    return ContentType.objects.get_for_model(obj, for_concrete_model=False)


def log_exception(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        # noinspection PyBroadException
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("AuditLog error")

    return inner


def _bulk_create_with_signals(logs):
    """Runs bulk_create and manually dispatches post_save — bulk_create does not emit signals."""
    created = Log.objects.bulk_create(logs, batch_size=100)
    for log in created:
        post_save.send(sender=Log, instance=log, created=True, raw=False,
                       using='default', update_fields=None)
    return created


def _resolve_objs(objs):
    """
    Normalizes any iterable into (objs, is_queryset, n).
    Querysets are detected by the presence of .values_list — avoids loading full instances
    when only PKs or a count are needed.
    """
    is_queryset = hasattr(objs, 'values_list')
    if not is_queryset and not isinstance(objs, (list, tuple)):
        objs = list(objs)
    n = objs.count() if is_queryset else len(objs)
    return objs, is_queryset, n


def _get_pks(objs, is_queryset):
    """Returns a list of PKs without loading full instances when possible."""
    if is_queryset:
        return list(objs.values_list('pk', flat=True))
    return [obj.pk for obj in objs]


class AuditLog:
    """
    Unified API for recording audit log events in xadmin.

    Bulk methods apply a threshold (BULK_LOG_THRESHOLD = 50):
    - n <= threshold: one log entry per object with full traceability
    - n >  threshold: single summary log with PKs in the message field
    """

    _changed_str = _lazy('Changed %s.')
    _and_str = _lazy('and')

    @staticmethod
    @log_exception
    def _log(request, flag, obj, message, **kwargs):
        """
        Creates and optionally persists a log entry.

        auto_save=False: returns the Log instance without saving — used by bulk methods
        to accumulate instances and persist them via bulk_create in a single query.
        """
        if request.user.is_authenticated:
            log = Log(
                user=request.user,
                ip_addr=request.META.get('REMOTE_ADDR'),
                action_flag=flag,
                message=message,
            )
            if obj:
                log.content_type = _get_content_type_for_model(obj)
                log.object_id = obj.pk
                log.object_repr = Truncator(force_str(obj)).chars(log.object_repr_length)
            if kwargs.get('auto_save', True):
                log.save()
            return log

    @classmethod
    def create(cls, request, obj, **kwargs):
        """Records a 'create' event."""
        return cls._log(request, 'create', obj, kwargs.pop('message', ''), **kwargs)

    @classmethod
    def update(cls, request, obj, fields=None, **kwargs):
        """Records an 'update' event. Auto-generates message from field names if not provided."""
        message = kwargs.pop('message', None)
        if message is None:
            message = cls._changed_str % get_text_list(fields, cls._and_str)
        return cls._log(request, 'update', obj, message, **kwargs)

    @classmethod
    @log_exception
    def bulk_update(cls, request, objs, fields=None, **kwargs):
        """
        Records 'update' events for multiple objects.

        - n <= BULK_LOG_THRESHOLD: one log per object, bulk_create + manual post_save dispatch
        - n >  BULK_LOG_THRESHOLD: single summary log with field names and PKs in message
        """
        caller_message = kwargs.pop('message', None)
        objs, is_queryset, n = _resolve_objs(objs)

        if n <= BULK_LOG_THRESHOLD:
            logs = []
            for obj in objs:
                log = cls.update(request, obj, fields=fields, auto_save=False, message=caller_message, **kwargs)
                if log is not None:
                    logs.append(log)
            return _bulk_create_with_signals(logs)

        # summary log: fetch only PKs to avoid materializing full instances
        pks = _get_pks(objs, is_queryset)
        fields_str = ', '.join(str(f) for f in fields) if fields else _('multiple fields')
        message = _('Batch updated %(count)d objects - %(fields)s [pks: %(pks)s]') % {
            'count': n,
            'fields': fields_str,
            'pks': ', '.join(str(pk) for pk in pks),
        }
        return cls._log(request, 'update', None, message, **kwargs)

    @classmethod
    def delete(cls, request, obj, **kwargs):
        """Records a 'delete' event."""
        return cls._log(request, 'delete', obj, kwargs.pop('message', ''), **kwargs)

    @classmethod
    @log_exception
    def bulk_delete(cls, request, objs, **kwargs):
        """
        Records 'delete' events for multiple objects.

        Must be called BEFORE the actual deletion — Django zeroes PKs on deleted instances.

        - n <= BULK_LOG_THRESHOLD: one log per object, bulk_create + manual post_save dispatch
        - n >  BULK_LOG_THRESHOLD: single summary log with PKs in message
        """
        kwargs.pop('message', None)
        objs, is_queryset, n = _resolve_objs(objs)

        if n <= BULK_LOG_THRESHOLD:
            logs = []
            for obj in objs:
                log = cls.delete(request, obj, auto_save=False, **kwargs)
                if log is not None:
                    logs.append(log)
            return _bulk_create_with_signals(logs)

        # summary log: fetch only PKs to avoid materializing full instances
        pks = _get_pks(objs, is_queryset)
        message = _('Batch deleted %(count)d objects [pks: %(pks)s]') % {
            'count': n,
            'pks': ', '.join(str(pk) for pk in pks),
        }
        return cls._log(request, 'delete', None, message, **kwargs)
