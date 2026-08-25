# coding=utf-8
"""XadminFormHelper aggregates top-of-form errors without assuming their type. #7366

This helper was a line-by-line copy of plus_hidra's ``HidraFormHelper``. #7365 fixed
two defects on the plus_hidra side after a reCAPTCHA rejection turned the riocard login
into an HTTP 500, and the two copies diverged. The fix now lives here, and plus_hidra
subclasses it instead of carrying its own copy.

The two fragile assumptions, both in ``_get_form_top_errors``:

  - ``non_field_errors().copy()`` assumed ``form._errors[NON_FIELD_ERRORS]`` is always
    an ``ErrorList``. ``BaseForm.non_field_errors()`` is just a ``self.errors.get(...)``
    -- it returns whatever is in the dict. A form that stores a bare ``ValidationError``
    there (the riocard captcha did) took the whole page down instead of showing the
    form error.
  - The formset arm called ``non_field_errors()``, which ``BaseFormSet`` does not have;
    the formset API is ``non_form_errors()``.

What these tests pin is the OUTPUT contract: a list of messages, tolerant of whatever
error container arrives. This is shared infrastructure -- it renders the admin login,
where the riocard site mixes a captcha form into every Hidra view -- so it cannot
depend on the concrete type it is handed.
"""
from crispy_forms.layout import Layout
from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.forms import BaseFormSet, formset_factory
from django.template import Context
from django.test import SimpleTestCase, override_settings

from xadmin.helpers import XadminFormHelper

FORMSET_REJECTION = 'formset level rejection'
FORM_REJECTION = 'form level rejection'


class _SummaryForm(forms.Form):
    """One visible field and one required hidden field.

    The hidden field is what exercises ``render_hidden_fields_errors``: an error on a
    field the user cannot see only surfaces if it is promoted to the top.
    """
    visible = forms.CharField(required=False)
    hidden = forms.CharField(widget=forms.HiddenInput)


class _SummarySetForm(forms.Form):
    name = forms.CharField(required=False)


class _RejectingFormSet(BaseFormSet):
    """Rejects the set as a whole -- this is what feeds non_form_errors()."""

    def clean(self):
        raise ValidationError(FORMSET_REJECTION)


_RejectingFormSetClass = formset_factory(
    _SummarySetForm, formset=_RejectingFormSet, extra=1)


def make_formset_data(name='filled'):
    """Management form plus one row: the minimum for a bound formset."""
    return {
        'form-TOTAL_FORMS': '1',
        'form-INITIAL_FORMS': '0',
        'form-MIN_NUM_FORMS': '0',
        'form-MAX_NUM_FORMS': '1000',
        'form-0-name': name,
    }


@override_settings(DEBUG=False)
class FormTopErrorsTests(SimpleTestCase):

    def _bound_form(self, **data):
        form = _SummaryForm(data=data)
        form.is_valid()          # runs full_clean, so _errors exists before we touch it
        return form

    def test_top_errors_flatten_a_raw_validation_error_in_the_error_dict(self):
        """The exact shape that produced the 500."""
        form = self._bound_form(hidden='filled')
        form._errors[NON_FIELD_ERRORS] = ValidationError('raw boom')

        top_errors = XadminFormHelper()._get_form_top_errors(form)

        self.assertEqual(
            list(top_errors), ['raw boom'],
            msg='a raw ValidationError must be flattened into the summary, not crash it')

    def test_top_errors_keep_non_field_errors_before_hidden_field_errors(self):
        form = self._bound_form(visible='typed')     # required 'hidden' missing
        form.add_error(None, FORM_REJECTION)
        required_message = str(form.fields['hidden'].error_messages['required'])

        top_errors = list(XadminFormHelper()._get_form_top_errors(form))

        self.assertEqual(len(top_errors), 2)
        self.assertEqual(top_errors[0], FORM_REJECTION,
                         msg='non-field errors come first in the summary')
        self.assertEqual(top_errors[1], required_message,
                         msg='hidden field errors are appended after the non-field ones')

    def test_top_errors_are_empty_when_the_form_has_no_errors(self):
        form = self._bound_form(visible='typed', hidden='filled')
        self.assertEqual(list(XadminFormHelper()._get_form_top_errors(form)), [])


@override_settings(DEBUG=False)
class FormSetTopErrorsTests(SimpleTestCase):
    """crispy never hands the whole formset to render_layout -- it iterates the inner
    forms -- so this arm is not exercised in production today. It sits behind a live
    isinstance check, so the test proves it works rather than freezing a "dead code"
    verdict into a guard.
    """

    def test_formset_top_errors_come_from_non_form_errors(self):
        formset = _RejectingFormSetClass(data=make_formset_data())

        top_errors = list(
            XadminFormHelper()._get_form_top_errors(formset, is_formset=True))

        self.assertIn(FORMSET_REJECTION, top_errors,
                      msg='BaseFormSet has no non_field_errors(); the formset API is '
                          'non_form_errors()')


@override_settings(DEBUG=False)
class RenderLayoutTests(SimpleTestCase):
    """The context keys the bootstrap4/errors*.html templates consume."""

    def _helper(self):
        helper = XadminFormHelper()
        helper.layout = Layout()
        return helper

    def test_render_layout_publishes_form_summary_errors(self):
        form = _SummaryForm(data={'visible': 'typed'})
        form.is_valid()
        context = Context({})
        self._helper().render_layout(form, context)
        self.assertIn('form_summary_errors', context)

    def test_render_layout_publishes_formset_summary_errors(self):
        formset = _RejectingFormSetClass(data=make_formset_data())
        context = Context({})
        self._helper().render_layout(formset, context)
        self.assertIn('formset_summary_errors', context)

    def test_render_layout_publishes_nothing_for_an_unbound_form(self):
        context = Context({})
        self._helper().render_layout(_SummaryForm(), context)
        self.assertNotIn('form_summary_errors', context)


@override_settings(DEBUG=False)
class LoggerExtensionPointTests(SimpleTestCase):
    """The one thing plus_hidra needs to vary, and the reason it can now subclass.

    The two copies were identical apart from the hardening above and where the
    hidden-field warning is logged: this package uses the stdlib logger, while
    plus_hidra routes it through ``plugin.logger``, which the project requires. Exposing
    that as an override is what lets HidraFormHelper drop its copy of the whole class.
    """

    def test_hidden_field_errors_go_through_the_overridable_logger(self):
        captured = []

        class _CapturingHelper(XadminFormHelper):
            def get_logger(self):
                class _Logger:
                    @staticmethod
                    def warning(message, params):
                        captured.append(params)
                return _Logger()

        form = _SummaryForm(data={'visible': 'typed'})   # hidden is required, missing
        form.is_valid()

        errors = _CapturingHelper()._get_form_errors(form)

        self.assertEqual(len(errors), 1, msg='the hidden field error must be promoted')
        self.assertEqual(len(captured), 1,
                         msg='a subclass must be able to redirect the warning without '
                             'copying _get_form_errors')
        self.assertEqual(captured[0]['field_name'], 'hidden')
