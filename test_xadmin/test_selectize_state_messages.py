# coding=utf-8
"""The ``state_messages`` selectize plugin is wired wherever selectize is. #7605

Selectize 0.15.2 ships no state text at all: while ``load`` runs the user sees nothing,
an empty result set silently closes the dropdown (``refreshOptions``, selectize.js:2441)
and there is no notion of a minimum query length. select2 covered those with its i18n
catalog; the host project decided to keep selectize only, so the messages become a
plugin of the fork -- ``xadmin/static/xadmin/js/xadmin.selectize.state_messages.js`` --
registered with ``Selectize.define`` and shipped by the ``selectize`` and ``select``
vendor aliases, so any page that loads the vendor also has the plugin.

The project has no JavaScript test runner. These are contract guards over the wiring
(alias entries, file presence, plugin name, gettext usage, no string-built HTML); the
behaviour itself is validated in the browser.

Run (the fork suite runs under the host project's runner):
    docker compose exec django bash /opt/project/docker-project/app-gunicorn/dev-manage.sh \
        testcover test_xadmin.test_selectize_state_messages --keepdb --verbosity 2
"""
import os
import re

from django.test import SimpleTestCase

import xadmin

PLUGIN_PATH = 'xadmin/js/xadmin.selectize.state_messages.js'
STATIC_ROOT = os.path.join(os.path.dirname(xadmin.__file__), 'static')


def static_file(relative_path):
    return os.path.join(STATIC_ROOT, relative_path)


def read_static(relative_path):
    with open(static_file(relative_path), encoding='utf-8') as handle:
        return handle.read()


def index_of(items, needle):
    """Index of the first entry containing ``needle`` or -1."""
    for position, item in enumerate(items):
        if needle in item:
            return position
    return -1


class VendorAliasWiringTests(SimpleTestCase):
    """Both aliases that bring selectize must bring the plugin, after the vendor."""

    def assert_alias_has_plugin_after_vendor(self, alias, mode):
        from xadmin.vendors import vendors

        js = vendors[alias]['js'][mode]
        vendor_at = index_of(js, 'vendor/selectize/js/selectize')
        plugin_at = index_of(js, PLUGIN_PATH)
        self.assertNotEqual(plugin_at, -1,
                            msg='%s/%s must load the state_messages plugin' % (alias, mode))
        self.assertGreater(plugin_at, vendor_at,
                           msg='the plugin calls Selectize.define, so selectize.js must come first')

    def test_selectize_alias_dev(self):
        self.assert_alias_has_plugin_after_vendor('selectize', 'dev')

    def test_selectize_alias_production(self):
        self.assert_alias_has_plugin_after_vendor('selectize', 'production')

    def test_select_alias_dev(self):
        self.assert_alias_has_plugin_after_vendor('select', 'dev')

    def test_select_alias_production(self):
        self.assert_alias_has_plugin_after_vendor('select', 'production')


class PluginFileTests(SimpleTestCase):
    """What the plugin file itself must and must not contain."""

    def test_plugin_file_exists(self):
        self.assertTrue(os.path.exists(static_file(PLUGIN_PATH)),
                        msg='%s must exist in the fork static tree' % PLUGIN_PATH)

    def test_plugin_is_registered_under_the_expected_name(self):
        source = read_static(PLUGIN_PATH)
        self.assertRegex(source, r'Selectize\.define\(\s*["\']state_messages["\']',
                         msg='initializers ask for "state_messages"; the name is the contract')

    def test_every_message_goes_through_gettext(self):
        source = read_static(PLUGIN_PATH)
        for text in ('Searching', 'No results found', 'Type at least'):
            self.assertRegex(source, r'gettext\(\s*["\'][^"\']*' + re.escape(text),
                             msg='"%s" must be translatable via the jsi18n catalog' % text)

    def test_no_html_is_built_from_strings(self):
        # CSP discipline of the host project: markup comes from a template engine, never
        # from string concatenation into innerHTML/.html()
        source = read_static(PLUGIN_PATH)
        self.assertNotIn('innerHTML', source)
        for match in re.finditer(r'\.html\(([^)]*)\)', source):
            self.assertIn('renderString', match.group(0),
                          msg='.html() may only receive rendered template output')


class InitializerOptInTests(SimpleTestCase):
    """The fork's own initializer asks for the plugin in both of its branches."""

    def test_widget_select_asks_for_the_plugin_in_both_branches(self):
        source = read_static('xadmin/js/xadmin.widget.select.js')
        self.assertGreaterEqual(source.count('state_messages'), 2,
                                msg='both the plain and the .select-search branches must opt in')
