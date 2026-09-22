# coding=utf-8
"""``dependent-select``: a choice field whose options follow another field of the same form.

Declared by the admin, resolved by the plugin::

    class ArticleAdmin:
        style_fields = {'state': 'dependent-select'}
        dependent_fields = {'state': 'section'}

        def get_state_choices(self, section):
            # (value, label) pairs. ``section`` is the parent instance when the parent is a
            # relation, the raw posted value otherwise, and None when it is empty or gone.
            ...

Life cycle of the child field:

- the add form renders it with no option: the options arrive by ajax once the parent has
  a value (``xadmin.widget.select.js``, branch ``select.dependent-select``);
- the change form renders it with the choices of the instance's parent, so the saved
  value shows without waiting for a request;
- the admin's own URL answers the widget's GET (``_dependent_field`` + ``_dependent_parent``)
  with ``{"items": [{"id", "name"}], "initial"}``. ``initial`` is the formfield initial and
  only travels on creation: a default computed server-side (a workflow rule, say) lands on
  the empty select instead of costing a round trip through "this field is required";
- on POST the choices are recalculated from the posted parent before the form validates,
  which is what rejects a child value that belongs to another parent.

The parent reaches ``get_<field>_choices`` resolved by primary key and nothing else: the
access rule (which parents this user may see, which options they unlock) belongs to that
callback, exactly as it did when the mechanism lived in the host project. The endpoint
itself runs inside the add/change view, after its permission checks.

Only fields with ``choices`` are supported as children: a relation child needs a queryset,
not choices, and no admin asks for that yet. (#7612)
"""
from django import forms
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.http import JsonResponse

from xadmin.sites import site
from xadmin.util import is_ajax, vendor
from xadmin.views import BaseAdminPlugin, ModelFormAdminView

STYLE = 'dependent-select'


class DependentSelectWidget(forms.Select):
    """A plain <select> tagged for the dependent branch of the single select initializer."""

    def __init__(self, attrs=None, choices=()):
        attrs = dict(attrs or {})
        attrs['class'] = ('%s %s' % (attrs.get('class', ''), STYLE)).strip()
        super().__init__(attrs=attrs, choices=choices)

    @property
    def media(self):
        return vendor('select.js', 'select.css', 'xadmin.widget.select.js')


class DependentSelectPlugin(BaseAdminPlugin):
    style = STYLE
    # Filled from the admin options by the site: only attributes the plugin already declares
    # are copied (sites.py, _get_merge_attrs), and methods never are -- which is why the
    # choices callback is looked up on the admin view instead.
    style_fields = {}
    dependent_fields = {}
    FIELD_PARAM = '_dependent_field'
    PARENT_PARAM = '_dependent_parent'

    def init_request(self, *args, **kwargs):
        return self.style in (self.style_fields or {}).values()

    def setup(self, *args, **kwargs):
        # names styled during this request; valid_forms recalculates exactly these
        self.dependent = []

    # -- configuration ------------------------------------------------------------------

    def parent_name_for(self, field_name):
        try:
            return self.dependent_fields[field_name]
        except (KeyError, TypeError):
            raise ImproperlyConfigured(
                "'%s' uses the %s style but dependent_fields does not name its parent"
                % (field_name, self.style))

    def choices_method_for(self, field_name):
        method = getattr(self.admin_view, 'get_%s_choices' % field_name, None)
        if not callable(method):
            raise ImproperlyConfigured(
                "'%s' uses the %s style: the admin must define get_%s_choices(parent)"
                % (field_name, self.style, field_name))
        return method

    def choices_for(self, field_name, parent):
        return list(self.choices_method_for(field_name)(parent))

    # -- parent resolution --------------------------------------------------------------

    def resolve_parent(self, parent_name, raw):
        """The parent as ``get_<field>_choices`` expects it.

        An instance when the parent is a relation, the raw value otherwise; None when the
        value is empty or points at a row that no longer exists.
        """
        if raw in (None, ''):
            return None
        parent_field = self.opts.get_field(parent_name)
        if not parent_field.is_relation:
            return raw
        try:
            return parent_field.related_model._default_manager.get(pk=raw)
        except (ObjectDoesNotExist, ValueError, TypeError):
            return None

    @staticmethod
    def parent_from_instance(obj, parent_name):
        # a recovered object (reversion) may point at a parent that was deleted meanwhile
        try:
            return getattr(obj, parent_name)
        except ObjectDoesNotExist:
            return None

    # -- hooks ----------------------------------------------------------------------------

    def get_field_style(self, attrs, db_field, style, **kwargs):
        if style != self.style:
            return attrs
        if db_field.is_relation:
            raise ImproperlyConfigured(
                "'%s' uses the %s style but is a relation: only fields with choices are supported"
                % (db_field.name, self.style))
        parent_name = self.parent_name_for(db_field.name)
        # fail at render time, not at the first ajax call
        self.choices_method_for(db_field.name)

        obj = self.admin_view.org_obj
        if obj is None:
            choices = []
            url = self.admin_view.get_model_url(self.admin_view.model, 'add')
        else:
            choices = self.choices_for(db_field.name, self.parent_from_instance(obj, parent_name))
            url = self.admin_view.get_model_url(self.admin_view.model, 'change', obj.pk)

        widget = DependentSelectWidget(attrs={
            'data-field': db_field.name,
            'data-dependent-parent': parent_name,
            'data-search-url': url,
        })
        self.dependent.append(db_field.name)
        return dict(attrs or {}, choices=choices, widget=widget)

    def valid_forms(self, __):
        # The choices the form validates against are those of the parent that was POSTed,
        # read raw (the form has not been cleaned yet) and with the form prefix, so a
        # quick-form or an inline resolves the right input.
        form = self.admin_view.form_obj
        for field_name in self.dependent:
            field = form.fields.get(field_name)
            if field is None:
                continue
            parent_name = self.parent_name_for(field_name)
            raw = form.data.get(form.add_prefix(parent_name))
            field.choices = self.choices_for(field_name, self.resolve_parent(parent_name, raw))
        return __()

    def initial_for(self, field_name, items):
        """The value the client should pre-select when the field arrives empty.

        Creation only: on an edit the instance value rules and the endpoint must not opine.
        Read from the formfield, so the plugin stays ignorant of who computed it. ``0`` is a
        legitimate value, hence the explicit None checks.
        """
        if getattr(self.admin_view, 'org_obj', None) is not None:
            return None
        form = getattr(self.admin_view, 'form_obj', None)
        field = form.fields.get(field_name) if form is not None else None
        if field is None:
            return None
        initial = field.initial() if callable(field.initial) else field.initial
        if initial is None:
            return None
        return initial if any(initial == value for value, _label in items) else None

    def get_response(self, __, *args, **kwargs):
        """Answers the widget's GET on the admin's own URL; anything else falls through."""
        request = self.request
        field_name = request.GET.get(self.FIELD_PARAM)
        if not (field_name and is_ajax(request)):
            return __()
        if (self.style_fields or {}).get(field_name) != self.style:
            return JsonResponse({'error': "'%s' is not a %s field" % (field_name, self.style)}, status=400)
        parent_name = self.parent_name_for(field_name)
        parent = self.resolve_parent(parent_name, request.GET.get(self.PARENT_PARAM))
        items = self.choices_for(field_name, parent)
        return JsonResponse({
            'items': [{'id': value, 'name': str(label)} for value, label in items],
            'initial': self.initial_for(field_name, items),
        })


site.register_plugin(DependentSelectPlugin, ModelFormAdminView)
