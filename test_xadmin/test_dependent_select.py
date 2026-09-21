# coding=utf-8
"""``dependent-select``: a choice field whose options follow another field of the form. #7612

The host project had this as a private plugin welded to one parent (``section``): the
widget, the JSON endpoint on the admin's own URL, the choices recalculated on POST and the
``initial`` applied on creation. ``DependentSelectPlugin`` is that mechanism with the parent
declared by the admin::

    style_fields = {'state': 'dependent-select'}
    dependent_fields = {'state': 'section'}

    def get_state_choices(self, section):   # (value, label) pairs; section may be None
        ...

The plugin is exercised in isolation: the fork suite runs under the host project's runner
with migrations disabled, and any TestCase touching the fixture app tables errors in
setUpClass there. Everything below is SimpleTestCase with a stand-in admin view.

Run:
    docker compose exec django bash /opt/project/docker-project/app-gunicorn/dev-manage.sh \
        testcover test_xadmin.test_dependent_select --keepdb --verbosity 2
"""
import json
from types import SimpleNamespace
from unittest import mock

from django import forms
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db import models
from django.test import RequestFactory, SimpleTestCase

from test_xadmin.fixtureapp.models import Author, Book

STYLE = 'dependent-select'
STATE_CHOICES = [(0, 'Approved'), (1, 'Author'), (2, 'Revision')]


def state_field():
    field = models.IntegerField(choices=STATE_CHOICES)
    field.set_attributes_from_name('state')
    return field


def make_plugin(style_fields=None, dependent_fields=None, org_obj=None, choices=None, form_obj=None,
                model=Book, request=None):
    """Plugin isolado, como o xadmin o monta: atributos do admin copiados para a instância."""
    from xadmin.plugins.dependent import DependentSelectPlugin

    plugin = DependentSelectPlugin.__new__(DependentSelectPlugin)
    plugin.style_fields = {'state': STYLE} if style_fields is None else style_fields
    plugin.dependent_fields = {'state': 'author'} if dependent_fields is None else dependent_fields
    admin_view = SimpleNamespace(
        org_obj=org_obj, model=model, form_obj=form_obj,
        get_model_url=lambda model, name, *args: '/x/%s/%s/' % (name, '/'.join(str(a) for a in args)),
    )
    if choices is not None:
        admin_view.get_state_choices = choices
    plugin.admin_view = admin_view
    plugin.model = model
    plugin.opts = model._meta
    plugin.request = request
    plugin.setup()
    return plugin


class ActivationTests(SimpleTestCase):

    def test_inactive_without_the_style(self):
        plugin = make_plugin(style_fields={'state': 'radio'})
        self.assertFalse(plugin.init_request())

    def test_active_when_a_field_declares_the_style(self):
        self.assertTrue(make_plugin().init_request())

    def test_builtin_plugin_list_carries_the_module(self):
        from xadmin.plugins import PLUGINS
        self.assertIn('dependent', PLUGINS, msg='register_builtin_plugins imports what is listed here')


class FieldStyleTests(SimpleTestCase):

    def test_other_styles_pass_through(self):
        plugin = make_plugin(choices=lambda parent: STATE_CHOICES)
        attrs = {'widget': 'keep-me'}
        self.assertIs(plugin.get_field_style(attrs, state_field(), 'radio'), attrs)

    def test_creation_renders_an_empty_select_pointing_at_the_add_url(self):
        from xadmin.plugins.dependent import DependentSelectWidget

        plugin = make_plugin(choices=lambda parent: STATE_CHOICES)
        attrs = plugin.get_field_style({}, state_field(), STYLE)

        self.assertEqual(attrs['choices'], [], msg='options arrive by ajax once the parent is known')
        widget = attrs['widget']
        self.assertIsInstance(widget, DependentSelectWidget)
        self.assertIn('dependent-select', widget.attrs['class'])
        self.assertEqual(widget.attrs['data-field'], 'state')
        self.assertEqual(widget.attrs['data-dependent-parent'], 'author')
        self.assertEqual(widget.attrs['data-search-url'], '/x/add//')
        self.assertEqual(plugin.dependent, ['state'])

    def test_edition_renders_the_choices_of_the_instance_parent(self):
        parent = Author(name='ana')
        obj = Book(title='b', author=parent)
        obj.pk = 7
        seen = []

        def choices(value):
            seen.append(value)
            return STATE_CHOICES[:2]

        plugin = make_plugin(org_obj=obj, choices=choices)
        attrs = plugin.get_field_style({}, state_field(), STYLE)

        self.assertEqual(seen, [parent])
        self.assertEqual(attrs['choices'], STATE_CHOICES[:2])
        self.assertEqual(attrs['widget'].attrs['data-search-url'], '/x/change/7/')

    def test_edition_survives_a_parent_that_no_longer_exists(self):
        # recover de um objeto cujo pai foi apagado: o getattr do FK levanta DoesNotExist
        obj = Book(title='b')
        obj.pk = 7
        obj.author_id = 999
        seen = []

        def choices(value):
            seen.append(value)
            return []

        plugin = make_plugin(org_obj=obj, choices=choices)
        # the FK descriptor would hit the database: the gone parent is simulated on the class
        with mock.patch.object(Book, 'author', new_callable=mock.PropertyMock,
                               side_effect=Author.DoesNotExist):
            attrs = plugin.get_field_style({}, state_field(), STYLE)

        self.assertEqual(seen, [None])
        self.assertEqual(attrs['choices'], [])

    def test_parent_not_declared_is_a_configuration_error(self):
        plugin = make_plugin(dependent_fields={}, choices=lambda parent: [])
        with self.assertRaises(ImproperlyConfigured):
            plugin.get_field_style({}, state_field(), STYLE)

    def test_missing_choices_callback_is_a_configuration_error(self):
        plugin = make_plugin()  # sem get_state_choices
        with self.assertRaises(ImproperlyConfigured):
            plugin.get_field_style({}, state_field(), STYLE)

    def test_a_relation_child_is_out_of_scope(self):
        plugin = make_plugin(dependent_fields={'author': 'state'}, choices=lambda parent: [])
        with self.assertRaises(ImproperlyConfigured):
            plugin.get_field_style({}, Book._meta.get_field('author'), STYLE)


class ResolveParentTests(SimpleTestCase):

    def test_relation_with_no_value_is_none(self):
        plugin = make_plugin()
        self.assertIsNone(plugin.resolve_parent('author', ''))
        self.assertIsNone(plugin.resolve_parent('author', None))

    def test_relation_with_an_unknown_pk_is_none(self):
        plugin = make_plugin()
        with mock.patch.object(Author._default_manager.__class__, 'get',
                               side_effect=Author.DoesNotExist, create=True):
            self.assertIsNone(plugin.resolve_parent('author', '999'))

    def test_relation_with_a_valid_pk_is_the_object(self):
        plugin = make_plugin()
        parent = Author(name='ana')
        with mock.patch.object(Author._default_manager.__class__, 'get', return_value=parent, create=True):
            self.assertIs(plugin.resolve_parent('author', '3'), parent)

    def test_plain_parent_is_the_raw_value(self):
        plugin = make_plugin()
        self.assertEqual(plugin.resolve_parent('title', 'x'), 'x')
        self.assertIsNone(plugin.resolve_parent('title', ''))


class ValidFormsTests(SimpleTestCase):
    """As choices do filho são recalculadas pelo POST do pai antes da unica validacao."""

    class Form(forms.Form):
        title = forms.CharField(required=False)
        state = forms.TypedChoiceField(choices=[], coerce=int)

    def test_choices_follow_the_posted_parent_before_validation(self):
        form = self.Form(data={'f-title': 'x', 'f-state': '1'}, prefix='f')
        seen = {}

        def inner():
            seen['choices'] = list(form.fields['state'].choices)
            return 'inner-result'

        plugin = make_plugin(dependent_fields={'state': 'title'}, form_obj=form,
                             choices=lambda parent: STATE_CHOICES if parent == 'x' else [])
        plugin.dependent.append('state')

        self.assertEqual(plugin.valid_forms(inner), 'inner-result')
        self.assertEqual(seen['choices'], STATE_CHOICES,
                         msg='the prefixed parent value must drive the choices seen by is_valid()')

    def test_fields_not_styled_are_left_alone(self):
        form = self.Form(data={'title': 'x', 'state': '1'})
        plugin = make_plugin(dependent_fields={'state': 'title'}, form_obj=form,
                             choices=lambda parent: STATE_CHOICES)
        plugin.valid_forms(lambda: True)
        self.assertEqual(list(form.fields['state'].choices), [])


class InitialTests(SimpleTestCase):

    def form_with_initial(self, initial):
        form = forms.Form()
        form.fields['state'] = forms.TypedChoiceField(choices=STATE_CHOICES, coerce=int, initial=initial)
        return form

    def test_creation_returns_the_formfield_initial_when_offered(self):
        plugin = make_plugin(form_obj=self.form_with_initial(1))
        self.assertEqual(plugin.initial_for('state', STATE_CHOICES), 1)

    def test_zero_is_a_legitimate_initial(self):
        plugin = make_plugin(form_obj=self.form_with_initial(0))
        self.assertEqual(plugin.initial_for('state', STATE_CHOICES), 0)

    def test_callable_initial_is_resolved(self):
        plugin = make_plugin(form_obj=self.form_with_initial(lambda: 2))
        self.assertEqual(plugin.initial_for('state', STATE_CHOICES), 2)

    def test_initial_outside_the_items_is_dropped(self):
        plugin = make_plugin(form_obj=self.form_with_initial(5))
        self.assertIsNone(plugin.initial_for('state', STATE_CHOICES))

    def test_unknown_field_is_none(self):
        plugin = make_plugin(form_obj=self.form_with_initial(1))
        self.assertIsNone(plugin.initial_for('missing', STATE_CHOICES))

    def test_edition_never_offers_an_initial(self):
        plugin = make_plugin(form_obj=self.form_with_initial(1), org_obj=Book(title='b'))
        self.assertIsNone(plugin.initial_for('state', STATE_CHOICES))


class ResponseTests(SimpleTestCase):

    def get(self, params, ajax=True):
        extra = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
        return RequestFactory().get('/x/add/', params, **extra)

    def test_a_plain_get_falls_through(self):
        plugin = make_plugin(request=self.get({'_dependent_field': 'state'}, ajax=False))
        self.assertEqual(plugin.get_response(lambda: 'page'), 'page')

    def test_an_ajax_get_without_the_marker_falls_through(self):
        plugin = make_plugin(request=self.get({'q': '1'}))
        self.assertEqual(plugin.get_response(lambda: 'page'), 'page')

    def test_a_field_not_declared_with_the_style_is_rejected(self):
        plugin = make_plugin(request=self.get({'_dependent_field': 'title', '_dependent_parent': '1'}),
                             choices=lambda parent: STATE_CHOICES)
        response = plugin.get_response(lambda: 'page')
        self.assertEqual(response.status_code, 400)

    def test_a_declared_field_returns_items_and_initial(self):
        form = forms.Form()
        form.fields['state'] = forms.TypedChoiceField(choices=STATE_CHOICES, coerce=int, initial=0)
        parent = Author(name='ana')
        seen = []

        def choices(value):
            seen.append(value)
            return STATE_CHOICES[:2]

        plugin = make_plugin(request=self.get({'_dependent_field': 'state', '_dependent_parent': '3'}),
                             choices=choices, form_obj=form)
        with mock.patch.object(Author._default_manager.__class__, 'get', return_value=parent, create=True):
            response = plugin.get_response(lambda: 'page')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, [parent])
        self.assertEqual(json.loads(response.content.decode()), {
            'items': [{'id': 0, 'name': 'Approved'}, {'id': 1, 'name': 'Author'}],
            'initial': 0,
        })


class WidgetTests(SimpleTestCase):

    def test_widget_loads_the_single_initializer(self):
        from xadmin.plugins.dependent import DependentSelectWidget
        widget = DependentSelectWidget()
        self.assertIn('dependent-select', widget.attrs['class'])
        self.assertIn('xadmin.widget.select.js', str(widget.media))

    def test_widget_keeps_the_classes_it_is_given(self):
        from xadmin.plugins.dependent import DependentSelectWidget
        widget = DependentSelectWidget(attrs={'class': 'form-control'})
        self.assertIn('form-control', widget.attrs['class'])
        self.assertIn('dependent-select', widget.attrs['class'])
