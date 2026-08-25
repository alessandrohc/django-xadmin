# coding=utf-8
"""The dashboard's "Target Model" dropdown lists the models that are registered.

This is the failure a status-code test cannot see. ``ModelBaseWidget`` declares

    model = ModelChoiceField(label=_('Target Model'), widget=exwidgets.AdminSelectWidget)

in its class body, and ``ModelChoiceField.__init__`` does ``self.widget.choices =
self.choices``. A class body runs at *import* time -- and ``xadmin.views.dashboard`` is
imported at the very top of ``autodiscover()``, long before the ``adminx`` modules that
actually populate ``site._registry``.

On Django 4.2 that was harmless: ``ChoiceWidget.choices`` was a plain instance attribute,
so the widget kept xadmin's lazy ``ModelChoiceIterator`` and re-walked the registry every
time it rendered. Django 5.0 turned ``ChoiceWidget.choices`` into a property whose setter
runs ``normalize_choices()``, which materialises an ordinary iterator into a ``list``
immediately -- freezing the dropdown to whatever was registered at import time.

Measured before the fix, on Django 5.2.17: the dropdown offered **1 of 8** registered
models. In the host project, which registers 259, it would offer one.

``normalize_choices()`` deliberately passes ``django.utils.choices.BaseChoiceIterator``
instances through untouched ("avoid prematurely normalizing iterators that should be
lazy"), so inheriting from it restores the 4.2 behaviour without changing 4.2, where the
base class does not exist and the fallback is ``object``.
"""
from django.test import SimpleTestCase

from xadmin import site
from xadmin.views.dashboard import ModelBaseWidget, ModelChoiceIterator


def registered_model_keys():
    return ['{0}.{1}'.format(model._meta.app_label, model._meta.model_name)
            for model in site._registry]


class TargetModelChoicesTests(SimpleTestCase):

    def field(self):
        # The form metaclass collects declared fields off the class, so the
        # class-body instance lives in base_fields rather than as an attribute.
        return ModelBaseWidget.base_fields['model']

    def test_dropdown_offers_every_registered_model(self):
        """The whole point: the class-body widget must not be frozen at import."""
        expected = registered_model_keys()
        self.assertTrue(expected, msg='the suite must register at least one model')

        offered = [key for key, _label in self.field().widget.choices]
        self.assertCountEqual(
            offered, expected,
            msg='the Target Model dropdown must offer every registered model; a frozen '
                'list here means the widget materialised choices at import time')

    def test_field_choices_are_lazy(self):
        """The field's own choices were always lazy; this pins that they stay so."""
        self.assertCountEqual(
            [key for key, _label in self.field().choices], registered_model_keys())

    def test_iterator_survives_widget_assignment(self):
        """The widget must still hold the lazy iterator, not a materialised list.

        This is the mechanism behind the test above, and it is what actually differs
        between Django 4.2 and 5.2. Asserting it directly means a future Django that
        changes normalize_choices() again fails here with a clear cause rather than as
        a mystery in the assertion above.
        """
        self.assertIsInstance(
            self.field().widget.choices, ModelChoiceIterator,
            msg='ChoiceWidget.choices normalised the iterator into a list; '
                'ModelChoiceIterator must derive from BaseChoiceIterator on Django 5+')
