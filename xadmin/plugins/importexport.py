# coding=utf-8

"""
Author:zcyuefan
Topic:django-import-export plugin for xadmin to help importing and exporting data using .csv/.xls/.../.json files

Use:
+++ settings.py +++
INSTALLED_APPS = (
	...
	'import_export',
)

+++ model.py +++
from django.db import models

class Foo(models.Model):
	name = models.CharField(max_length=64)
	description = models.TextField()

+++ adminx.py +++
import xadmin
from import_export import resources
from .models import Foo

class FooResource(resources.ModelResource):

    class Meta:
		model = Foo
		# fields = ('name', 'description',)
		# exclude = ()


@xadmin.sites.register(Foo)
class FooAdmin:
	import_export_args = {'import_resource_class': FooResource, 'export_resource_class': FooResource}

++++++++++++++++
More info about django-import-export please refer https://github.com/django-import-export/django-import-export
"""
import threading
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE, DELETION
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.http import HttpResponseRedirect, HttpResponse
from django.template import loader
from django.template.response import TemplateResponse
from django.urls.base import reverse
from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _
from import_export.forms import (ImportForm, ConfirmImportForm, ExportForm)
from import_export.resources import modelresource_factory
from import_export.results import RowResult
from import_export.signals import post_export, post_import
from import_export.tmp_storages import TempFolderStorage
from xadmin.plugins.utils import get_context_dict
from xadmin.sites import site, AdminPath
from xadmin.views import BaseAdminPlugin, ListAdminView, ModelAdminView
from xadmin.views.base import csrf_protect_m, filter_hook

TMP_STORAGE_CLASS = getattr(settings, 'IMPORT_EXPORT_TMP_STORAGE_CLASS', TempFolderStorage)
SKIP_ADMIN_LOG = getattr(settings, 'IMPORT_EXPORT_SKIP_ADMIN_LOG', False)


class ImportMenuPlugin(BaseAdminPlugin):
	import_export_args = {}

	def init_request(self, *args, **kwargs):
		return bool(self.import_export_args.get('import_resource_class'))

	def block_top_toolbar(self, context, nodes):
		has_change_perm = self.has_model_perm(self.model, 'change')
		has_add_perm = self.has_model_perm(self.model, 'add')
		if has_change_perm and has_add_perm:
			model_info = (self.opts.app_label, self.opts.model_name)
			import_url = reverse('xadmin:%s_%s_import' % model_info, current_app=self.admin_site.name)
			context = get_context_dict(context or {})  # no error!
			context.update({
				'import_url': import_url,
			})
			nodes.append(loader.render_to_string('xadmin/blocks/model_list.top_toolbar.importexport.import.html',
			                                     context=context))


class ImportBaseView(ModelAdminView):
	""" ImportBaseView """
	resource_class = None
	import_export_args = {}
	#: template for import view
	import_template_name = 'xadmin/import_export/import.html'
	#: resource class
	#: available import formats
	@property
	def formats(self):
		# Lazy import: base_formats evaluates DEFAULT_FORMATS at load time,
		# which triggers tablib._xlsx -> openpyxl (~31 MB RSS at boot). Deferring
		# to the first real access avoids that cost during django.setup().
		from import_export.formats.base_formats import DEFAULT_FORMATS
		return DEFAULT_FORMATS
	#: import data encoding
	from_encoding = "utf-8"
	skip_admin_log = None
	# storage class for saving temporary files
	tmp_storage_class = None

	def get_skip_admin_log(self):
		if self.skip_admin_log is None:
			return SKIP_ADMIN_LOG
		else:
			return self.skip_admin_log

	def get_tmp_storage_class(self):
		if self.tmp_storage_class is None:
			return TMP_STORAGE_CLASS
		else:
			return self.tmp_storage_class

	def get_resource_kwargs(self, request, *args, **kwargs):
		return {}

	def get_import_resource_kwargs(self, request, *args, **kwargs):
		return self.get_resource_kwargs(request, *args, **kwargs)

	def get_resource_class(self, usage):
		if usage == 'import':
			return self.import_export_args.get('import_resource_class') if self.import_export_args.get(
				'import_resource_class') else modelresource_factory(self.model)
		elif usage == 'export':
			return self.import_export_args.get('export_resource_class') if self.import_export_args.get(
				'export_resource_class') else modelresource_factory(self.model)
		else:
			return modelresource_factory(self.model)

	def get_import_resource_class(self):
		"""
		Returns ResourceClass to use for import.
		"""
		return self.process_import_resource(self.get_resource_class(usage='import'))

	def process_import_resource(self, resource):
		"""
		Returns processed ResourceClass to use for import.
		Override to custom your own process
		"""
		return resource

	def get_import_formats(self):
		"""
		Returns available import formats.
		"""
		return [f for f in self.formats if f().can_import()]


class ImportView(ImportBaseView):

	def get_media(self):
		media = super(ImportView, self).get_media()
		media = media + self.vendor('xadmin.plugin.importexport.css')
		return media

	@filter_hook
	def get(self, request, *args, **kwargs):
		if not (self.has_change_permission() and self.has_add_permission()):
			raise PermissionDenied

		# The class goes into a local because the 4.x ImportForm requires the LIST of
		# resources; it used to be discarded right after instantiating (#7396).
		resource_class = self.get_import_resource_class()
		resource = resource_class(**self.get_import_resource_kwargs(request, *args, **kwargs))

		context = super(ImportView, self).get_context()

		import_formats = self.get_import_formats()
		# django-import-export 4.x changed the signature to
		# ImportForm(formats, resources, **kwargs): `resources` became POSITIONAL and
		# mandatory (_init_resources raises ValueError on an empty list), and *args is
		# gone, so data/files only go in BY NAME (#7396).
		form = ImportForm(import_formats,
		                  [resource_class],
		                  data=request.POST or None,
		                  files=request.FILES or None)

		context['title'] = _("Import") + ' ' + self.opts.verbose_name
		context['form'] = form
		context['opts'] = self.model._meta
		context['fields'] = [f.column_name for f in resource.get_user_visible_fields()]

		request.current_app = self.admin_site.name
		return TemplateResponse(request, [self.import_template_name],
		                        context)

	@filter_hook
	@csrf_protect_m
	@transaction.atomic
	def post(self, request, *args, **kwargs):
		"""
			Perform a dry_run of the import to make sure the import will not
		result in errors.  If there where no error, save the user
		uploaded file to a local temp file that will be used by
		'process_import' for the actual import.
		"""
		if not (self.has_change_permission() and self.has_add_permission()):
			raise PermissionDenied

		# The class goes into a local because the 4.x ImportForm requires the LIST of
		# resources; it used to be discarded right after instantiating (#7396).
		resource_class = self.get_import_resource_class()
		resource = resource_class(**self.get_import_resource_kwargs(request, *args, **kwargs))

		context = super(ImportView, self).get_context()

		import_formats = self.get_import_formats()
		# django-import-export 4.x changed the signature to
		# ImportForm(formats, resources, **kwargs): `resources` became POSITIONAL and
		# mandatory (_init_resources raises ValueError on an empty list), and *args is
		# gone, so data/files only go in BY NAME (#7396).
		form = ImportForm(import_formats,
		                  [resource_class],
		                  data=request.POST or None,
		                  files=request.FILES or None)

		if request.POST and form.is_valid():
			# 4.x renamed the `input_format` field to `format`: it moved up into
			# ImportExportFormBase, shared with the export form (#7396).
			input_format = import_formats[
				int(form.cleaned_data['format'])
			]()
			import_file = form.cleaned_data['import_file']
			# first always write the uploaded file to disk as it may be a
			# memory file or else based on settings upload handlers
			# django-import-export 3.0 moved the read mode (and the encoding) out of the
			# save()/read() calls and into BaseStorage.__init__, so passing them as
			# positional arguments raises TypeError -- a deterministic 500 on every
			# upload. This mirrors what import_export.admin.ImportMixin does. See #7369.
			tmp_storage = self.get_tmp_storage_class()(
				encoding=None if input_format.is_binary() else self.from_encoding,
				read_mode=input_format.get_read_mode(),
			)
			data = bytes()
			for chunk in import_file.chunks():
				data += chunk

			tmp_storage.save(data)

			# then read the file, using the proper format-specific mode
			# warning, big files may exceed memory
			try:
				data = tmp_storage.read()
				if not input_format.is_binary() and self.from_encoding:
					data = force_str(data, self.from_encoding)
				dataset = input_format.create_dataset(data)
			except UnicodeDecodeError as e:
				return HttpResponse(_("<h1>Imported file has a wrong encoding: %s</h1>" % e))
			except Exception as e:
				return HttpResponse(_("<h1>{0!s} encountered while trying to read file: {1:s}</h1>"
				                      .format(type(e).__name__, import_file.name)))

			result = resource.import_data(dataset, dry_run=True,
			                              raise_errors=False,
			                              file_name=import_file.name,
			                              user=request.user)

			context['result'] = result

			if not result.has_errors():
				context['confirm_form'] = ConfirmImportForm(initial={
					'import_file_name': tmp_storage.name,
					'original_file_name': import_file.name,
					# The 4.x ConfirmImportForm calls the field `format` as well (#7396).
					'format': form.cleaned_data['format'],
				})

		context['title'] = _("Import") + ' ' + self.opts.verbose_name
		context['form'] = form
		context['opts'] = self.model._meta
		context['fields'] = [f.column_name for f in resource.get_user_visible_fields()]

		request.current_app = self.admin_site.name
		return TemplateResponse(request, [self.import_template_name],
		                        context)


class ImportProcessView(ImportBaseView):

	@filter_hook
	@csrf_protect_m
	@transaction.atomic
	def post(self, request, *args, **kwargs):
		"""
		Perform the actual import action (after the user has confirmed he
		wishes to import)
		"""
		resource = self.get_import_resource_class()(**self.get_import_resource_kwargs(request, *args, **kwargs))

		confirm_form = ConfirmImportForm(request.POST)
		if confirm_form.is_valid():
			import_formats = self.get_import_formats()
			# Same rename: the ConfirmImportForm hidden field became `format` in 4.x (#7396).
			input_format = import_formats[
				int(confirm_form.cleaned_data['format'])
			]()
			# Same signature change as in ImportView above (#7369).
			tmp_storage = self.get_tmp_storage_class()(
				name=confirm_form.cleaned_data['import_file_name'],
				encoding=None if input_format.is_binary() else self.from_encoding,
				read_mode=input_format.get_read_mode(),
			)
			data = tmp_storage.read()
			if not input_format.is_binary() and self.from_encoding:
				data = force_str(data, self.from_encoding)
			dataset = input_format.create_dataset(data)

			result = resource.import_data(dataset, dry_run=False,
			                              raise_errors=True,
			                              file_name=confirm_form.cleaned_data['original_file_name'],
			                              user=request.user)

			if not self.get_skip_admin_log():
				# Add imported objects to LogEntry
				logentry_map = {
					RowResult.IMPORT_TYPE_NEW: ADDITION,
					RowResult.IMPORT_TYPE_UPDATE: CHANGE,
					RowResult.IMPORT_TYPE_DELETE: DELETION,
				}
				content_type_id = ContentType.objects.get_for_model(self.model).pk
				for row in result:
					if row.import_type != row.IMPORT_TYPE_ERROR and row.import_type != row.IMPORT_TYPE_SKIP:
						# create() rather than log_action(): the latter was deprecated in
						# Django 5.1 (RemovedInDjango60Warning) and this call sits inside
						# the per-row loop, so a 10k-row import emitted 10k warnings.
						# This is byte-for-byte what log_action does -- str() on the id
						# and the 200-char truncation included; the json.dumps branch does
						# not apply because change_message here is always a string. Its
						# replacement, log_actions(), is NOT equivalent: it changes
						# object_repr, the content type and the grouping. See #7369.
						LogEntry.objects.create(
							user_id=request.user.pk,
							content_type_id=content_type_id,
							object_id=str(row.object_id),
							object_repr=row.object_repr[:200],
							action_flag=logentry_map[row.import_type],
							change_message="%s through import_export" % row.import_type,
						)
			success_message = str(_('Import finished')) + ' , ' + str(_('Add')) + ' : %d' % result.totals[
				RowResult.IMPORT_TYPE_NEW] + ' , ' + str(_('Update')) + ' : %d' % result.totals[
				                  RowResult.IMPORT_TYPE_UPDATE]

			messages.success(request, success_message)
			tmp_storage.remove()

			post_import.send(sender=None, model=self.model)
			model_info = (self.opts.app_label, self.opts.model_name)
			url = reverse('xadmin:%s_%s_changelist' % model_info,
			              current_app=self.admin_site.name)
			return HttpResponseRedirect(url)


class ExportMixin:
	#: resource class
	resource_class = None
	#: template for change_list view
	change_list_template = None
	import_export_args = {}
	#: template for export view
	# export_template_name = 'xadmin/import_export/export.html'
	#: available export formats
	@property
	def formats(self):
		# Lazy import — see comment on ImportBaseView.formats
		from import_export.formats.base_formats import DEFAULT_FORMATS
		return DEFAULT_FORMATS
	#: export data encoding
	to_encoding = "utf-8"
	list_select_related = None

	def get_resource_kwargs(self, request, *args, **kwargs):
		return {}

	def get_export_resource_kwargs(self, request, *args, **kwargs):
		return self.get_resource_kwargs(request, *args, **kwargs)

	def get_resource_class(self, usage):
		if usage == 'import':
			return self.import_export_args.get('import_resource_class') if self.import_export_args.get(
				'import_resource_class') else modelresource_factory(self.model)
		elif usage == 'export':
			return self.import_export_args.get('export_resource_class') if self.import_export_args.get(
				'export_resource_class') else modelresource_factory(self.model)
		else:
			return modelresource_factory(self.model)

	def get_export_resource_class(self):
		"""
		Returns ResourceClass to use for export.
		"""
		return self.get_resource_class(usage='export')

	def get_export_formats(self):
		"""
		Returns available export formats.
		"""
		return [f for f in self.formats if f().can_export()]

	def get_export_filename(self, file_format):
		date_str = datetime.now().strftime('%Y-%m-%d-%H%M%S')
		filename = "%s-%s.%s" % (force_str(self.opts.verbose_name,
		                                    encoding='utf-8'),
		                         date_str,
		                         file_format.get_extension())
		return filename

	def get_export_queryset(self, request, context):
		"""
		Returns export queryset.

		Default implementation respects applied search and filters.
		"""
		# scope = self.request.POST.get('_select_across', False) == '1'
		scope = request.GET.get('scope')
		select_across = request.GET.get('_select_across', False) == '1'
		selected = request.GET.get('_selected_actions', '')
		if scope == 'all':
			# Consider all filtered data
			queryset_all = request.GET.get('queryset_all', '')
			if queryset_all == "filtered":
				queryset = self.admin_view.get_list_queryset()
			else:
				queryset = self.admin_view.queryset()
		elif scope == 'header_only':
			queryset = []
		elif scope == 'selected':
			if not select_across:
				selected = selected.strip()
				if selected:
					selected_pk = selected.split(',')
					queryset = self.admin_view.queryset().filter(pk__in=selected_pk)
				else:
					queryset = self.admin_view.queryset().none()
			else:
				queryset = self.admin_view.queryset()
		else:
			queryset = [r['object'] for r in context['results']]
		return queryset

	def get_export_data(self, file_format, queryset, *args, **kwargs):
		"""
		Returns file_format representation for given queryset.
		"""
		request = kwargs.pop("request")
		resource_class = self.get_export_resource_class()
		data = resource_class(**self.get_export_resource_kwargs(request)).export(queryset, *args, **kwargs)
		export_data = file_format.export_data(data)
		return export_data


class ExportMenuPlugin(ExportMixin, BaseAdminPlugin):
	import_export_args = {}

	# Media
	def get_media(self, media):
		return media + self.vendor('xadmin.plugin.importexport.css', 'xadmin.plugin.importexport.js')

	def init_request(self, *args, **kwargs):
		return bool(self.import_export_args.get('export_resource_class'))

	@staticmethod
	def _form_bootstrap_styles(form):
		"""set bootstrap styles"""
		attrs = {'class': 'form-control'}
		# `file_format` was the field name in 3.x; in 4.x the format select comes from
		# ImportExportFormBase and is called `format`. Without this the `for` matches no
		# field at all and the modal select SILENTLY loses form-control/required (#7396).
		for field_name in ['format']:
			if field_name in form.fields:
				field = form.fields[field_name]
				if field.widget.attrs is None:
					field.widget.attrs = {}
				field.widget.attrs.update(attrs)
				# Makes the field required.
				if getattr(field, 'required'):
					field.widget.attrs['required'] = ''

	def block_top_toolbar(self, context, nodes):
		formats = self.get_export_formats()
		# In 4.x ExportForm inherits ImportExportFormBase, whose signature is
		# (formats, resources, **kwargs) -- `resources` is positional and mandatory
		# (#7396).
		form = ExportForm(formats, [self.get_export_resource_class()])
		self._form_bootstrap_styles(form)
		context = get_context_dict(context or {})  # no error!
		context.setdefault('export_to_email', bool(self.import_export_args.get('export_to_email', True)))
		context.update({
			'form': form,
			'opts': self.opts,
			'form_params': self.admin_view.get_form_params({'_action_': 'export'})
		})
		template_name = self.import_export_args.get('template',
		                                            'xadmin/blocks/model_list.top_toolbar.importexport.export.html')
		nodes.append(loader.render_to_string(template_name,
		                                     context=context))


class ExportPlugin(ExportMixin, BaseAdminPlugin):
	export_email_config = {}

	def init_request(self, *args, **kwargs):
		return self.request.GET.get('_action_') == 'export'

	def send_mail(self, user, request, **kwargs):
		"""Sends the file with data by email to the user"""
		host = request.get_host()
		email_config = {
			'subject': _('Exported file delivery'),
			'message': _('Sent from address {0!s}. The file is attached.').format(host),
			'from_email': settings.DEFAULT_FROM_EMAIL,
			'recipient_list': [user.email],
			'fail_silently': True,
			'html_message': False
		}
		email_config.update(self.export_email_config)

		def send_mail_async(config):
			file_format = kwargs['file_format']
			content = self.get_export_data(file_format,
			                               kwargs['queryset'],
			                               request=request)
			content_type = file_format.get_content_type()
			filename = self.get_export_filename(file_format)

			html_message = config.pop('html_message', False)
			fail_silently = config.pop('fail_silently', True)

			# compat
			config['body'] = config.pop('message', '')
			config['to'] = config.pop('recipient_list', None)

			mail = EmailMultiAlternatives(**config)
			if html_message:
				mail.attach_alternative(html_message, 'text/html')

			mail.attach(filename, content, content_type)
			mail.send(fail_silently=fail_silently)

		thargs = (email_config.copy(),)
		th = threading.Thread(target=send_mail_async, args=thargs)
		th.start()

	def send_mail_response(self, request, **kwargs):
		user = request.user
		email = user.email if hasattr(user, 'email') else None
		if isinstance(email, str) and email.strip():
			self.send_mail(user, request, **kwargs)
			messages.success(request, (_("The file is sent to your email: ") + f"<strong>{email}</strong>"))
		else:
			messages.warning(request, _("Your account does not have an email address."))
		return HttpResponseRedirect(request.path)

	def get_response(self, response, context, *args, **kwargs):
		has_view_perm = self.has_model_perm(self.model, 'view')
		if not has_view_perm:
			raise PermissionDenied

		# The modal issues its GET using the form's FIELD NAME, which went from
		# `file_format` to `format` in 4.x. The old name stays accepted so an export URL
		# hand-built or bookmarked before #7396 keeps working.
		export_format = (self.request.GET.get('format')
		                 or self.request.GET.get('file_format'))
		scope = self.request.GET.get('scope')

		if not export_format:
			messages.warning(self.request, _('You must select an export format.'))
			return HttpResponseRedirect(self.request.path)
		elif scope == 'selected' and not self.request.GET.get('_selected_actions', '').strip():
			messages.warning(self.request, _('You need to select items for export.'))
			return HttpResponseRedirect(self.request.path)
		else:
			formats = self.get_export_formats()
			file_format = formats[int(export_format)]()
			queryset = self.get_export_queryset(self.request, context)

			# The export takes place in the background, so you do not have to wait.
			if self.request.GET.get('result-action', '') == 'sendmail':
				return self.send_mail_response(
					self.request,
					file_format=file_format,
					queryset=queryset)

			# Follow normal flow if it is not to send by email
			export_data = self.get_export_data(file_format, queryset, request=self.request)
			content_type = file_format.get_content_type()

			# Django 1.7 uses the content_type kwarg instead of mimetype
			try:
				response = HttpResponse(export_data, content_type=content_type)
			except TypeError:
				response = HttpResponse(export_data, mimetype=content_type)
			response['Content-Disposition'] = 'attachment; filename=%s' % (
				self.get_export_filename(file_format),
			)
			post_export.send(sender=None, model=self.model)
			return response

#
site.register_modelview(AdminPath('import/', ImportView, name='%s_%s_import'))
site.register_modelview(AdminPath('process_import/', ImportProcessView, name='%s_%s_process_import'))
site.register_plugin(ImportMenuPlugin, ListAdminView)
site.register_plugin(ExportMenuPlugin, ListAdminView)
site.register_plugin(ExportPlugin, ListAdminView)
