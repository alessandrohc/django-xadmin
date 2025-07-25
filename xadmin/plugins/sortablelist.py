# coding: utf-8
"""
Make items sortable by drag-drop in list view. Diffierent from
builtin plugin sortable, it touches model field indeed intead
of only for display.
"""
from xadmin.views.list import ResultHeader

from django.db import transaction
from django.template.loader import render_to_string

from xadmin.plugins.utils import get_context_dict
from xadmin.sites import site
from xadmin.views import (
	BaseAdminPlugin, ModelAdminView, ListAdminView
)
from xadmin.views.base import csrf_protect_m


class SortableListPlugin(BaseAdminPlugin):
	list_order_field = None
	# Used in custom columns
	list_order_display_field = None

	def init_request(self, *args, **kwargs):
		return bool(self.list_order_field)

	@property
	def is_list_sortable(self):
		return True

	def result_header(self, header, field_name, row):
		new_header = ResultHeader(field_name, row)
		new_header.text = header.text
		new_header.attr = header.attr
		return new_header

	def result_row(self, __, obj):
		row = __()
		row.update({
			"tagattrs": "order-key=order_{}".format(obj.pk)
		})
		return row

	def result_item(self, item, obj, field_name, row):
		if self.is_list_sortable and field_name in (self.list_order_field,
		                                            self.list_order_display_field):
			item.btns.append('<a><i class="fas fa-arrows-alt"></i></a>')
		return item

	def get_context(self, context):
		context['save_order_url'] = self.get_model_url(self.admin_view.model, 'save_order')
		return context

	def block_top_toolbar(self, context, nodes):
		save_node = render_to_string(
			'xadmin/blocks/model_list.top_toolbar.saveorder.html',
			context=get_context_dict(context)
		)
		nodes.append(save_node)

	def get_media(self, media):
		if self.is_list_sortable:
			media = media + self.vendor('xadmin.plugin.sortablelist.js')
		return media


class SaveOrderView(ModelAdminView):
	list_order_field_inverse = False

	@csrf_protect_m
	@transaction.atomic
	def post(self, request):
		order_objs = request.POST.getlist("order[]")
		total_order_objs = len(order_objs)

		for index, pk in enumerate(order_objs):
			pk = int(pk)
			if self.list_order_field_inverse:
				order_value = total_order_objs - index
			else:
				order_value = index + 1

			self.save_order(pk, order_value)
		self.message_user('Alteração feita com sucesso', 'success')
		return self.render_response({})

	def save_order(self, pk, order_value):
		obj = self.model.objects.get(pk=pk)
		order_field = self.list_order_field
		is_order_changed = lambda x: getattr(x, order_field) != order_value

		if is_order_changed(obj):
			setattr(obj, order_field, order_value)
			obj.save(update_fields=[order_field])


site.register_plugin(SortableListPlugin, ListAdminView)
site.register_modelview(r'^save-order/$', SaveOrderView, name='%s_%s_save_order')
