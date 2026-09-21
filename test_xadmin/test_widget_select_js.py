# coding=utf-8
"""``xadmin.widget.select.js`` is the one initializer of every select in the admin. #7611

Until this delivery the file selectized single selects (plain and ``.select-search``)
and deliberately skipped ``[multiple]``. The host project filled the gap with a widget
swap plugin, a marker widget and two initializers of its own -- four artifacts and an
opt-out flag to do what one more branch here does. The multiple branch now lives in this
file with the product defaults the host had settled on, options can be added by attribute
(``data-selectize-plugins``, ``data-search-url``), and GET forms join the values of a
multiple select for the ``__in`` lookup.

The project has no JavaScript test runner: these are contract guards over the file.
Run (the fork suite runs under the host project's runner):
    docker compose exec django bash /opt/project/docker-project/app-gunicorn/dev-manage.sh \
        testcover test_xadmin.test_widget_select_js --keepdb --verbosity 2
"""
import os
import re

from django.test import SimpleTestCase

import xadmin
from xadmin.widgets import AdminSelectMultiple

WIDGET_JS = os.path.join(os.path.dirname(os.path.abspath(xadmin.__file__)),
                         'static', 'xadmin', 'js', 'xadmin.widget.select.js')


class WidgetSelectJsTests(SimpleTestCase):
    """assertTrue, not assertIn: a failure must not dump the whole file."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(WIDGET_JS, encoding='utf-8') as handle:
            cls.source = handle.read()

    def test_multiple_selects_have_their_own_branch(self):
        pattern = r"""find\(\s*["']select\[multiple[^"']*:not\(\.selectize-off\)"""
        self.assertTrue(re.search(pattern, self.source) is not None,
                        msg='a select[multiple] branch honouring selectize-off must exist')

    def test_multiple_branch_carries_the_product_defaults(self):
        for snippet in ('remove_button', 'respect_word_boundaries: false', 'maxOptions: null',
                        'hideSelected: true', 'closeAfterSelect: false'):
            self.assertTrue(snippet in self.source,
                            msg='%s: default settled by the host project (SEL-1) for multiples' % snippet)

    def test_vendor_plugins_can_be_added_by_attribute(self):
        self.assertTrue('selectize-plugins' in self.source,
                        msg='data-selectize-plugins lets a widget (sortedm2m drag_drop) ask for '
                            'vendor plugins without a script of its own')

    def test_the_remote_loader_is_shared_by_the_branches(self):
        self.assertEqual(self.source.count("'_q_'"), 1,
                         msg='one remote loader (the _q_/_cols contract) for fk-ajax and remote multiples')

    def test_autocomplete_neutralization_is_shared(self):
        matches = re.findall(r"""autocomplete['"]\s*,\s*['"]off""", self.source)
        self.assertEqual(len(matches), 1,
                         msg='the autocomplete="new-password" workaround must live in one helper')

    def test_every_branch_asks_for_state_messages(self):
        self.assertGreaterEqual(self.source.count('state_messages'), 3,
                                msg='plain, .select-search and [multiple] branches all opt in')

    def test_dependent_selects_have_their_own_branch(self):
        # #7612: a child select reloads its options from the admin URL when its parent changes
        self.assertTrue(re.search(r"""find\(\s*["']select\.dependent-select""", self.source) is not None,
                        msg='the dependent-select branch must exist')
        self.assertTrue(':not(.dependent-select)' in self.source,
                        msg='the plain branch must leave the dependent select to its own branch')

    def test_dependent_branch_contract(self):
        for snippet in ('dependent-parent', "'change'", '_dependent_field', '_dependent_parent',
                        "labelField: 'name'", "getValue() === ''", 'setValue('):
            self.assertTrue(snippet in self.source, msg='%s: part of the dependent-select contract' % snippet)

    def test_get_forms_join_multiple_values_for_the_in_lookup(self):
        self.assertTrue('formdata' in self.source,
                        msg='changelist filter forms submit __in=1,2; xadmin keeps the last GET value otherwise')
        self.assertTrue(re.search(r"""join\(\s*['"],['"]\s*\)""", self.source) is not None)


class AdminSelectMultipleTests(SimpleTestCase):
    """The default M2M widget already brings the marker class and the initializer."""

    def test_widget_marks_the_select_and_loads_the_initializer(self):
        widget = AdminSelectMultiple()
        self.assertIn('select-multi', widget.attrs.get('class', ''))
        self.assertIn('xadmin.widget.select.js', str(widget.media))
