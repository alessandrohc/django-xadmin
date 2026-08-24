# coding=utf-8
"""The boolean list filter offers "Unknown" exactly when the field is nullable. #7369

The filter used to key the third choice off ``isinstance(field, NullBooleanField)``.
That class is kept by Django only for historical migrations -- nobody declares it any
more -- so the branch was dead and the "Unknown" option never appeared, on any field.

The nullability is what the option actually means: the query it builds is
``__isnull=True``. ``NullBooleanField`` forced ``null=True`` in its own ``__init__``, so
keying off ``field.null`` is equivalent for the legacy class and additionally works for
a modern ``BooleanField(null=True)`` -- which is what everyone writes today.

This is the one behavioural change in the batch: on a nullable boolean the filter now
offers a third option where it offered two.
"""
from django.test import TestCase

from test_xadmin.fixtureapp.models import Author


class BooleanFieldFilterTests(TestCase):

    def _choices(self, field_name):
        from xadmin.filters import BooleanFieldListFilter

        field = Author._meta.get_field(field_name)

        class _Filter(BooleanFieldListFilter):
            def __init__(self, f):
                self.field = f
                self.field_path = f.name
                self.lookup_exact_name = '%s__exact' % f.name
                self.lookup_isnull_name = '%s__isnull' % f.name
                self.lookup_exact_val = ''
                self.lookup_isnull_val = ''

            def query_string(self, *args, **kwargs):
                return ''

        return [choice['display'] for choice in _Filter(field).choices()]

    def test_a_nullable_boolean_offers_unknown(self):
        displays = [str(d) for d in self._choices('is_featured')]
        self.assertIn('Unknown', displays,
                      msg='a nullable boolean must be filterable on NULL')

    def test_a_non_nullable_boolean_does_not_offer_unknown(self):
        displays = [str(d) for d in self._choices('is_active')]
        self.assertNotIn('Unknown', displays,
                         msg='a non-nullable boolean has no NULL rows to filter')

    def test_both_still_offer_all_yes_and_no(self):
        for name in ('is_active', 'is_featured'):
            with self.subTest(field=name):
                displays = [str(d) for d in self._choices(name)]
                for expected in ('All', 'Yes', 'No'):
                    self.assertIn(expected, displays)
