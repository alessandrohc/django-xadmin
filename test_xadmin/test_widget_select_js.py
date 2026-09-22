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

    def test_hide_empty_measures_the_items_not_the_labelled_empty_option(self):
        # #7614: a widget asking for hide-empty AND empty-label (riocard FAQ) must still hide the
        # group while the parent has no children; the labelled empty option is not an item
        self.assertTrue("toggleClass('d-none', !hadItems)" in self.source,
                        msg='hide-empty is decided by the items the parent returned')
        self.assertTrue("toggleClass('d-none', items.length === 0)" not in self.source,
                        msg='measuring after the empty label went in never hides')

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


class SiteScopeTests(SimpleTestCase):
    """One file for both worlds (#7616).

    Inside ``form.exform`` (the admin templates declare it) every select is initialized;
    anywhere else only ``select.selectize`` is, with the semantics the host project's site
    initializer used to carry. The site file is gone, so these guards are the contract.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(WIDGET_JS, encoding='utf-8') as handle:
            cls.source = handle.read()

    def test_the_scope_is_decided_by_the_form_class(self):
        self.assertTrue("closest('form.exform')" in self.source,
                        msg='admin scope: the container is, or lives inside, a form.exform (quick-form wrap, formset row)')
        self.assertTrue('select.selectize:not(.selectize-off)' in self.source,
                        msg='site scope: opt-in by the selectize class of the Hidra widgets')

    def test_site_semantics_are_carried(self):
        for snippet in ('allowEmptyOption', 'selectize-dont-allow-empty', 'delimiter', 'persist: false',
                        'hideInput', '.destroy()'):
            self.assertTrue(snippet in self.source, msg='%s: behaviour of the site initializer' % snippet)

    def test_site_helpers_are_attached_to_the_instance(self):
        for snippet in ('hidra_add_loading', 'hidra_remove_loading', 'hidra_show_error', 'hidra_clear_options',
                        'selectize-class-container-loading'):
            self.assertTrue(snippet in self.source, msg='%s: other site scripts call these helpers' % snippet)

    def test_named_nunjucks_templates_render_and_the_admin_env_is_guarded(self):
        self.assertTrue('data-selectize-nunjucks-render-item' in self.source)
        self.assertTrue('nunjucks.render(' in self.source, msg='site widgets name precompiled .njk templates')
        self.assertTrue(re.search(r"""\$\.fn\.nunjucks_env\s*(&&|\))""", self.source) is not None,
                        msg='$.fn.nunjucks_env only exists where the admin loads the nunjucks alias')

    def test_the_dependent_branch_is_the_unified_one(self):
        for snippet in ('dependent-hide-empty', 'dependent-empty-label', '_dependentRestore', '_dependentBound'):
            self.assertTrue(snippet in self.source, msg='%s: extras of the site branch (SEL-8) now serve both worlds' % snippet)

    def test_the_file_survives_a_page_without_exform(self):
        self.assertTrue(re.search(r"""if\s*\(\s*!\s*\$\.fn\.exform\s*\)""", self.source) is not None,
                        msg='guard before registering in exform.renders')

    def test_everything_mounts_through_the_single_helper(self):
        self.assertEqual(self.source.count('.selectize('), 1,
                         msg='every branch, admin or site, goes through mount()')
