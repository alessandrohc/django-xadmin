import datetime
import decimal
import json

from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DEFAULT_DB_ALIAS, connections, models
from django.db.models.base import ModelBase
from django.db.models.signals import post_migrate
from django.urls.base import reverse
from django.utils import timezone
from django.utils.functional import classproperty
from django.utils.encoding import smart_str
from django.utils.translation import gettext_lazy as _, gettext

AUTH_USER_MODEL = getattr(settings, 'AUTH_USER_MODEL', 'auth.User')


def add_view_permissions(sender, **kwargs):
	"""
	This post_migrate hook ensures a "view" permission exists for every
	content type. Uses bulk operations to avoid N+1 queries:
	  1. Fetch all content types (1 query)
	  2. Fetch all existing view_* permissions (1 query)
	  3. Bulk-create only the missing ones (1 query)

	Every query is bound to the alias post_migrate hands us. Ignoring it made the hook
	read and write the default connection no matter which database was migrated, which
	is silently wrong on a multi-database install. See #7205.
	"""
	# The database this post_migrate is actually about.
	db = kwargs.get('using', DEFAULT_DB_ALIAS)

	# fetch all content types in a single query
	all_content_types = ContentType.objects.using(db).all()

	# build a set of (content_type_id, codename) for O(1) lookup — 1 query
	existing_view_permissions = set(
		Permission.objects.using(db).filter(codename__startswith="view_").values_list(
			"content_type_id", "codename"
		)
	)

	# determine which permissions are missing — pure in-memory comparison
	permissions_to_create = [
		Permission(
			content_type=content_type,
			codename="view_%s" % content_type.model,
			name="Can view %s" % content_type.name,
		)
		for content_type in all_content_types
		if (content_type.pk, "view_%s" % content_type.model) not in existing_view_permissions
	]

	# bulk-create all missing permissions in a single query
	if permissions_to_create:
		# ignore_conflicts is gated on the backend, not assumed. It compiles to
		# ON CONFLICT DO NOTHING (Postgres) / INSERT IGNORE (MySQL); mssql-django
		# declares supports_ignore_conflicts = False and Django then raises
		# NotSupportedError before touching the database.
		#
		# That raise is not a local failure. This receiver is connected at module
		# import, while contenttypes and auth connect theirs from AppConfig.ready() --
		# a later phase -- so this one fires FIRST, and Django 4.2's Signal.send() is a
		# list comprehension: whatever raises here takes create_contenttypes and
		# create_permissions down with it, leaving newly migrated models with no
		# content type and no permissions at all. See #7205.
		#
		# The flag is redundant anyway: the comprehension above already filtered out
		# every view_* permission that exists, so a sequential migrate has no conflict
		# left to ignore. It stays only as belt-and-braces against a concurrent run.
		Permission.objects.using(db).bulk_create(
			permissions_to_create,
			ignore_conflicts=connections[db].features.supports_ignore_conflicts,
		)


# ensure view permissions exist after every migration
post_migrate.connect(add_view_permissions, dispatch_uid="xadmin_add_view_permissions")


class Bookmark(models.Model):
	title = models.CharField(_('Title'), max_length=128)
	user = models.ForeignKey(AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name=_("user"), blank=True, null=True)
	url_name = models.CharField(_('Url Name'), max_length=64)
	content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
	query = models.CharField(_('Query String'), max_length=1000, blank=True)
	is_share = models.BooleanField(_('Is Shared'), default=False)

	@property
	def url(self):
		base_url = reverse(self.url_name)
		if self.query:
			base_url = base_url + '?' + self.query
		return base_url

	def __str__(self):
		return self.title

	class Meta:
		verbose_name = _('Bookmark')
		verbose_name_plural = _('Bookmarks')


class JSONEncoder(DjangoJSONEncoder):

	def default(self, o):
		if isinstance(o, datetime.datetime):
			return o.strftime('%Y-%m-%d %H:%M:%S')
		elif isinstance(o, datetime.date):
			return o.strftime('%Y-%m-%d')
		elif isinstance(o, decimal.Decimal):
			return str(o)
		elif isinstance(o, ModelBase):
			return '%s.%s' % (o._meta.app_label, o._meta.model_name)
		else:
			try:
				return super(JSONEncoder, self).default(o)
			except Exception:
				return smart_str(o)


class UserSettings(models.Model):
	user = models.ForeignKey(AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name=_("user"))
	key = models.CharField(_('Settings Key'), max_length=256)
	value = models.TextField(_('Settings Content'))

	def json_value(self):
		return json.loads(self.value)

	def set_json(self, obj):
		self.value = json.dumps(obj, cls=JSONEncoder, ensure_ascii=False)

	def __str__(self):
		return "%s %s" % (self.user, self.key)

	class Meta:
		verbose_name = _('User Setting')
		verbose_name_plural = _('User Settings')


class UserWidget(models.Model):
	user = models.ForeignKey(AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name=_("user"))
	page_id = models.CharField(_("Page"), max_length=256)
	widget_type = models.CharField(_("Widget Type"), max_length=50)
	value = models.TextField(_("Widget Params"))

	def get_value(self):
		value = json.loads(self.value)
		value['id'] = self.id
		value['type'] = self.widget_type
		return value

	def set_value(self, obj):
		self.value = json.dumps(obj, cls=JSONEncoder, ensure_ascii=False)

	def save(self, *args, **kwargs):
		created = self.pk is None
		super(UserWidget, self).save(*args, **kwargs)
		if created:
			try:
				portal_pos = UserSettings.objects.get(
					user=self.user, key="dashboard:%s:pos" % self.page_id)
				portal_pos.value = "%s,%s" % (self.pk, portal_pos.value) if portal_pos.value else self.pk
				portal_pos.save()
			except Exception:
				pass

	def __str__(self):
		return "%s %s widget" % (self.user, self.widget_type)

	class Meta:
		verbose_name = _('User Widget')
		verbose_name_plural = _('User Widgets')


class Log(models.Model):
	action_time = models.DateTimeField(
		_('action time'),
		default=timezone.now,
		editable=False,
	)
	user = models.ForeignKey(
		AUTH_USER_MODEL,
		models.CASCADE,
		verbose_name=_('user'),
	)
	ip_addr = models.GenericIPAddressField(_('action ip'), blank=True, null=True)
	content_type = models.ForeignKey(
		ContentType,
		models.SET_NULL,
		verbose_name=_('content type'),
		blank=True, null=True,
	)
	object_id = models.TextField(_('object id'), blank=True, null=True)
	object_repr = models.CharField(_('object repr'), max_length=200)
	action_flag = models.CharField(_('action flag'), max_length=32)
	message = models.TextField(_('change message'), blank=True)

	class Meta:
		verbose_name = _('log entry')
		verbose_name_plural = _('log entries')
		ordering = ('-action_time',)

	@classproperty
	def object_repr_length(cls):
		"""The maximum number of characters supported by the field"""
		return cls._meta.get_field("object_repr").max_length

	def __repr__(self):
		return smart_str(self.action_time)

	def __str__(self):
		# update/change already embeds the change message inline — keep it as is.
		if self.action_flag in ('update', 'change'):
			return gettext('Changed "%(object)s" - %(changes)s') % {
				'object': self.object_repr,
				'changes': self.message,
			}
		if self.action_flag == 'create':
			text = gettext('Added "%(object)s".') % {'object': self.object_repr}
		elif self.action_flag == 'delete':
			text = gettext('Deleted "%(object)s."') % {'object': self.object_repr} \
				if self.object_repr else gettext('Deleted object.')
		elif self.action_flag == 'move' and self.object_repr:
			text = gettext('Moved "%(object)s."') % {'object': self.object_repr}
		elif self.action_flag == 'copy' and self.object_repr:
			text = gettext('Copied "%(object)s."') % {'object': self.object_repr}
		elif self.action_flag == 'duplicate' and self.object_repr:
			text = gettext('Duplicated "%(object)s."') % {'object': self.object_repr}
		else:
			return self.message

		# always append the change message (description) when present — the separator
		# is a literal, not a translatable string.
		if self.message:
			text = '%s - %s' % (text, self.message)
		return text

	def get_edited_object(self):
		"""Returns the edited object represented by this log entry"""
		return self.content_type.get_object_for_this_type(pk=self.object_id)
