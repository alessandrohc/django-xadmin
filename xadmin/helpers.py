import logging

from crispy_forms.helper import FormHelper
from crispy_forms.utils import TEMPLATE_PACK
from django.conf import settings
from django.forms import BaseFormSet
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class XadminFormHelper(FormHelper):
    """FormHelper subclass that surfaces hidden-field validation errors.

    When a bound form contains errors on hidden fields, users normally cannot
    see what went wrong because the field is not rendered visually.  This
    helper collects those errors and injects them into the template context as
    ``form_summary_errors`` (for regular forms) or ``formset_summary_errors``
    (for formsets) so that the ``bootstrap4/errors.html`` and
    ``bootstrap4/errors_formset.html`` templates can display them at the top
    of the form.

    Equivalent to ``HidraFormHelper`` from plus_hidra, but without external
    dependencies — suitable for use inside django-xadmin as a standalone package.
    """

    # Whether hidden-field errors are collected and shown.
    render_hidden_fields_errors = True

    @staticmethod
    def _bound_items(form):
        """Yield (name, bf) pairs, where bf is a BoundField object."""
        for name in form.fields:
            yield name, form[name]

    def _get_form_errors(self, form):
        """Extract and format validation errors from hidden fields.

        In DEBUG mode the field name is included in the message for easier
        debugging; in production only the user-facing error text is shown and
        the details are logged.
        """
        if not self.render_hidden_fields_errors:
            return []

        user_facing_errors = []
        for name, bound_field in self._bound_items(form):
            if bound_field.is_hidden and bound_field.errors:
                for error in bound_field.errors:
                    if settings.DEBUG:
                        message = _(
                            'Error in <strong>hidden field "%(name)s"</strong> - %(error)s'
                        ) % {"name": escape(name), "error": escape(error)}
                        user_facing_errors.append(mark_safe(message))
                    else:
                        user_facing_errors.append(escape(error))
                        logger.warning(
                            'Hidden field validation error on form %(form_class)s. '
                            'Field: "%(field_name)s", Error: "%(error)s"',
                            {
                                "form_class": form.__class__.__name__,
                                "field_name": name,
                                "error": error,
                            },
                        )
        return user_facing_errors

    def _get_form_top_errors(self, form_or_formset, is_formset=False):
        """Aggregate top-level errors: non-field errors + hidden-field errors."""
        top_errors = form_or_formset.non_field_errors().copy()

        forms_to_check = form_or_formset if is_formset else [form_or_formset]

        for form in forms_to_check:
            top_errors.extend(self._get_form_errors(form))

        return top_errors

    def render_layout(self, form, context, template_pack=TEMPLATE_PACK):
        # Inject aggregated errors into the context so that the custom
        # bootstrap4/errors*.html templates can render them.  This avoids
        # mutating the form's internal non_field_errors list.
        if form.is_bound:
            if is_formset := isinstance(form, BaseFormSet):
                context['formset_summary_errors'] = self._get_form_top_errors(
                    form, is_formset=is_formset
                )
            else:
                context['form_summary_errors'] = self._get_form_top_errors(form)
        return super().render_layout(form, context, template_pack=TEMPLATE_PACK)

    def get_attributes(self, template_pack=TEMPLATE_PACK, **kwargs):
        attrs = super().get_attributes(template_pack=template_pack, **kwargs)
        attrs['render_hidden_fields_errors'] = self.render_hidden_fields_errors
        return attrs
