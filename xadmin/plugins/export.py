# coding=utf-8
import copy
import datetime
import importlib.util
import sys

import io
import threading

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation
from django.core.mail import EmailMultiAlternatives
from django.db.models import BooleanField
from django.http import HttpResponse, HttpResponseRedirect
from django.template import loader
from django.utils.encoding import force_str, smart_str
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.utils.xmlutils import SimplerXMLGenerator

from xadmin.plugins.utils import get_context_dict
from xadmin.sites import site
from xadmin.util import json
from xadmin.views import BaseAdminPlugin, ListAdminView
from xadmin.views.list import ALL_VAR

# Probed, not imported. These are only needed inside the export methods, but
# importing them here to set the feature flags cost 12.0 MB and 98 modules in EVERY
# process -- Celery workers included -- for a feature most of them never touch.
# find_spec answers the same question at 0 MB and 0 modules (#7368).
has_xlwt = importlib.util.find_spec('xlwt') is not None
has_xlsxwriter = importlib.util.find_spec('xlsxwriter') is not None


class ExportMenuPlugin(BaseAdminPlugin):
	export_menu_block_template = 'xadmin/blocks/model_list.top_toolbar.exports.html'

	# No csv: a CSV carries no cell type, so a spreadsheet re-guesses every value on
	# open and a cell starting with = + - @ becomes a formula. It cannot be made safe
	# without disfiguring the data -- the usual apostrophe prefix would have hit the "-"
	# empty-cell placeholder on thousands of cells. The binary formats carry the type and
	# are safe by construction; xml and json are not evaluated at all. See #7369.
	list_export = ('xlsx', 'xls', 'xml', 'json')
	export_names = {'xlsx': 'Excel 2007', 'xls': 'Excel',
	                'xml': 'XML', 'json': 'JSON'}
	export_to_email = True

	def init_request(self, *args, **kwargs):
		self.list_export = [
			f for f in self.list_export
			if (f != 'xlsx' or has_xlsxwriter) and (f != 'xls' or has_xlwt)]

	def block_top_toolbar(self, context, nodes):
		if self.list_export:
			context.update({
				'show_export_all': self.admin_view.paginator.count > self.admin_view.list_per_page and not ALL_VAR in self.admin_view.request.GET,
				'form_params': self.admin_view.get_form_params({'_do_': 'export'}, ('export_type',)),
				'export_types': [{'type': et, 'name': self.export_names[et]} for et in self.list_export],
				'export_to_email': self.export_to_email
			})
			nodes.append(loader.render_to_string(self.export_menu_block_template,
			                                     context=get_context_dict(context)))


class ExportPlugin(BaseAdminPlugin):
	export_mimes = {'xlsx': 'application/vnd.ms-excel',
	                'xls': 'application/vnd.ms-excel',
	                'xml': 'application/xhtml+xml', 'json': 'application/json'}

	export_unicode_encoding = "utf-8"
	export_email_config = {}

	def init_request(self, *args, **kwargs):
		return self.request.GET.get('_do_') == 'export'

	def _format_value(self, o):
		if (o.field is None and getattr(o.attr, 'boolean', False)) or \
				(o.field and isinstance(o.field, BooleanField)):
			value = o.value
		elif str(o.text).startswith("<span class='text-muted'>"):
			value = escape(str(o.text)[25:-7])
		else:
			value = escape(str(o.text))
		return value

	def _options_is_on(self, name):
		"""Checks if the option value is on. Return bool"""
		return getattr(self.request, self.request.method).get(name, 'off') == 'on'

	def _get_objects(self, context):
		headers = [c for c in context['result_headers'].cells if c.export]
		rows = context['results']

		return [dict([
			(force_str(headers[i].text), self._format_value(o)) for i, o in
			enumerate(filter(lambda c: getattr(c, 'export', False), r.cells))]) for r in rows]

	def _get_datas(self, context):
		rows = context['results']

		new_rows = [[self._format_value(o) for o in
		             filter(lambda c: getattr(c, 'export', False), r.cells)] for r in rows]
		new_rows.insert(0, [force_str(c.text) for c in context['result_headers'].cells if c.export])
		return new_rows

	def get_xlsx_export(self, context):
		datas = self._get_datas(context)
		output = io.BytesIO()
		export_header = self._options_is_on('export_xlsx_header')
		model_name = self.opts.verbose_name
		# Imported at the point of use: see the find_spec note at the top of the module.
		import xlsxwriter

		# strings_to_formulas=False: xlsxwriter promotes a string starting with "=" to
		# write_formula(), so the workbook carried a real <f> element that Excel evaluates
		# on open -- no CSV import dialog in between. This stores it as text instead. The
		# exported value does not change. See #7369.
		book = xlsxwriter.Workbook(output, {'strings_to_formulas': False})
		sheet = book.add_worksheet(
			"%s %s" % (_('Sheet'), force_str(model_name)))
		styles = {'datetime': book.add_format({'num_format': 'yyyy-mm-dd hh:mm:ss'}),
		          'date': book.add_format({'num_format': 'yyyy-mm-dd'}),
		          'time': book.add_format({'num_format': 'hh:mm:ss'}),
		          'header': book.add_format(
			          {'font': 'name Times New Roman', 'color': 'red', 'bold': 'on', 'num_format': '#,##0.00'}),
		          'default': book.add_format()}

		if not export_header:
			datas = datas[1:]
		for rowx, row in enumerate(datas):
			for colx, value in enumerate(row):
				if export_header and rowx == 0:
					cell_style = styles['header']
				else:
					if isinstance(value, datetime.datetime):
						cell_style = styles['datetime']
					elif isinstance(value, datetime.date):
						cell_style = styles['date']
					elif isinstance(value, datetime.time):
						cell_style = styles['time']
					else:
						cell_style = styles['default']
				sheet.write(rowx, colx, value, cell_style)
		book.close()

		output.seek(0)
		return output.getvalue()

	def get_xls_export(self, context):
		datas = self._get_datas(context)
		output = io.BytesIO()
		export_header = self._options_is_on('export_xls_header')
		model_name = self.opts.verbose_name
		# Imported at the point of use: see the find_spec note at the top of the module.
		import xlwt

		book = xlwt.Workbook(encoding=self.export_unicode_encoding)
		sheet = book.add_sheet("%s %s" % (_('Sheet'), force_str(model_name)))
		styles = {'datetime': xlwt.easyxf(num_format_str='yyyy-mm-dd hh:mm:ss'),
		          'date': xlwt.easyxf(num_format_str='yyyy-mm-dd'),
		          'time': xlwt.easyxf(num_format_str='hh:mm:ss'),
		          'header': xlwt.easyxf('font: name Times New Roman, color-index red, bold on',
		                                num_format_str='#,##0.00'),
		          'default': xlwt.Style.default_style}

		if not export_header:
			datas = datas[1:]
		for rowx, row in enumerate(datas):
			for colx, value in enumerate(row):
				if export_header and rowx == 0:
					cell_style = styles['header']
				else:
					if isinstance(value, datetime.datetime):
						cell_style = styles['datetime']
					elif isinstance(value, datetime.date):
						cell_style = styles['date']
					elif isinstance(value, datetime.time):
						cell_style = styles['time']
					else:
						cell_style = styles['default']
				sheet.write(rowx, colx, value, style=cell_style)
		book.save(output)

		output.seek(0)
		return output.getvalue()

	def get_xml_export(self, context):
		results = self._get_objects(context)

		stream = io.BytesIO()

		xml = SimplerXMLGenerator(stream, self.export_unicode_encoding)
		xml.startDocument()
		xml.startElement("objects", {})

		self._to_xml(xml, results)

		xml.endElement("objects")
		xml.endDocument()

		return stream.getvalue().split(b'\n')[1]

	def get_json_export(self, context):
		results = self._get_objects(context)
		return json.dumps({'objects': results}, ensure_ascii=False,
		                  indent=(self._options_is_on('export_json_format') and 4 or None))

	def send_mail(self, user, request, context):
		"""Send the data file by email"""
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

		def send_mail_async(config, data, context):
			filename, content, file_mimetype = self._get_file_spec(data, context)

			html_message = config.pop('html_message', False)
			fail_silently = config.pop('fail_silently', True)

			# compat
			config['body'] = config.pop('message', '')
			config['to'] = config.pop('recipient_list', None)

			mail = EmailMultiAlternatives(**config)
			if html_message:
				mail.attach_alternative(html_message, 'text/html')

			mail.attach(filename, content, file_mimetype)
			mail.send(fail_silently=fail_silently)

		thargs = (email_config.copy(),
		          copy.deepcopy(request.GET),
		          context.copy())
		th = threading.Thread(target=send_mail_async, args=thargs)
		th.start()

	def _get_file_spec(self, data, context):
		file_type = data.get('export_type', 'xlsx')
		# Validate before getattr: export_type comes straight from the query string, so
		# an unknown value used to reach getattr() and raise AttributeError -- an
		# unhandled 500 for any arbitrary ?export_type=. Same shape as the ?_fields=
		# defect in plugins/ajax.py. Checking against export_mimes also means a format
		# removed from the plugin (csv, #7369) stops being servable by a hand-built URL,
		# which matters because list_export only gates the MENU, not the endpoint.
		if file_type not in self.export_mimes:
			raise SuspiciousOperation(
				"Unsupported export format: %r" % file_type)
		content = getattr(self, 'get_%s_export' % file_type)(context)
		filename = "{0:s}.{1:s}".format(self.opts.verbose_name.replace(' ', '_'),
		                                file_type)
		file_mimetype = self.export_mimes[file_type]
		return filename, content, file_mimetype

	def get_response(self, response, context, *args, **kwargs):
		request = self.request
		if self._options_is_on('export_to_email'):
			user = request.user
			email = user.email if hasattr(user, 'email') else None
			if isinstance(email, str) and email.strip():
				self.send_mail(user, request, context)
				messages.success(request, (_("The file is sent to your email: ") + f"<strong>{email}</strong>"))
			else:
				messages.warning(request, _("Your account does not have an email address."))
			return HttpResponseRedirect(request.path)

		filename, content, file_mimetype = self._get_file_spec(request.GET, context)
		response = HttpResponse(content_type=f"{file_mimetype}; charset={self.export_unicode_encoding}")
		filename_format = f'attachment; filename="{filename}"'
		response['Content-Disposition'] = filename_format.encode(self.export_unicode_encoding)
		response.write(content)
		return response

	# View Methods
	def get_result_list(self, __):
		if self._options_is_on('all'):
			self.admin_view.list_per_page = sys.maxsize
		return __()

	def result_header(self, item, field_name, row):
		item.export = not item.attr or field_name == '__str__' or getattr(item.attr, 'allow_export', True)
		return item

	def result_item(self, item, obj, field_name, row):
		item.export = item.field or field_name == '__str__' or getattr(item.attr, 'allow_export', True)
		return item


site.register_plugin(ExportMenuPlugin, ListAdminView)
site.register_plugin(ExportPlugin, ListAdminView)
