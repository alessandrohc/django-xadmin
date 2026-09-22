# coding=utf-8
"""select2 leaves the fork: vendor, aliases, stylesheets and build pipeline. #7608

Since the host project moved every admin select to selectize (SEL-1 to SEL-4, #7604-#7607)
nothing instantiates select2 any more, yet the ``select`` alias still shipped
``select2.js`` + its i18n catalog on every FK/M2M widget and filter, and the vendor tree
(66 files) travelled in every release. This delivery removes the vendor and turns
``select`` into the very same object as ``selectize``: a host project that swaps the
selectize stylesheet for its theme (plus_base zeroes ``selectize['css']``) gets the swap
for ``select`` too, without knowing the alias exists.

``xstatic`` gets the fix that made an empty vendor list a bomb: ``[] or [files]`` became
``[[]]`` and ``f % {...}`` raised -- ``vendor('selectize.css')`` took the whole file
manager down once the host zeroed the list (measured in SEL-3).

The project has no JavaScript test runner; the file guards are contract guards over the
wiring. Run (the fork suite runs under the host project's runner):
    docker compose exec django bash /opt/project/docker-project/app-gunicorn/dev-manage.sh \
        testcover test_xadmin.test_select2_removal --keepdb --verbosity 2
"""
import json
import os

from django.test import SimpleTestCase

import xadmin
from xadmin import vendors as vendors_module
from xadmin.util import vendor

XADMIN_ROOT = os.path.dirname(os.path.abspath(xadmin.__file__))
STATIC_ROOT = os.path.join(XADMIN_ROOT, 'static', 'xadmin')

SOURCE_SUFFIXES = ('.py', '.js', '.css', '.json')
# not our source: third-party vendors, translation catalogs, caches
EXCLUDED_FRAGMENTS = (os.sep + 'vendor' + os.sep, os.sep + 'locale' + os.sep, '__pycache__')


def read_static(relative_path):
    with open(os.path.join(STATIC_ROOT, relative_path), encoding='utf-8') as handle:
        return handle.read()


def every_vendor_path():
    """Every static path declared in the vendors dict, whatever the nesting."""
    for alias, kinds in vendors_module.vendors.items():
        for kind, modes in kinds.items():
            if isinstance(modes, str):
                yield alias, modes
                continue
            for mode, files in modes.items():
                if isinstance(files, str):
                    yield alias, files
                else:
                    for path in files:
                        yield alias, path


def source_files():
    for dirpath, dirnames, filenames in os.walk(XADMIN_ROOT):
        if any(fragment in dirpath + os.sep for fragment in EXCLUDED_FRAGMENTS):
            continue
        for name in filenames:
            if name.endswith(SOURCE_SUFFIXES):
                yield os.path.join(dirpath, name)


class VendorAliasTests(SimpleTestCase):

    def test_the_select2_alias_is_gone(self):
        # assertTrue, not assertNotIn: a failure must not dump the whole vendors dict
        self.assertTrue('select2' not in vendors_module.vendors,
                        msg='both callers of the alias were retired by SEL-1 and SEL-3')

    def test_select_is_the_same_object_as_selectize(self):
        self.assertTrue(vendors_module.vendors['select'] is vendors_module.vendors['selectize'],
                        msg='select must alias selectize by reference so a host theme swap '
                            'of selectize css applies to both')

    def test_no_alias_path_points_at_select2(self):
        # the select2 i18n catalog lives under vendor/select2/, so the name covers it too
        # (other aliases have their own %(lang)s catalogs -- datatables -- and keep them)
        offenders = ['%s: %s' % (alias, path) for alias, path in every_vendor_path() if 'select2' in path]
        self.assertEqual(offenders, [], msg='select2 paths left in vendors: %s' % offenders)

    def test_select_still_brings_selectize_and_the_state_messages_plugin(self):
        for mode in ('dev', 'production'):
            js = vendors_module.vendors['select']['js'][mode]
            self.assertTrue(any('vendor/selectize/js/selectize' in path for path in js),
                            msg='select/%s must load selectize' % mode)
            self.assertTrue(any('xadmin.selectize.state_messages.js' in path for path in js),
                            msg='select/%s must load the state_messages plugin' % mode)


class EmptyVendorListTests(SimpleTestCase):
    """A host project may empty a css list (the theme ships the stylesheet)."""

    def zeroed_selectize_css(self):
        css = vendors_module.vendors['selectize']['css']
        saved = {mode: css[mode] for mode in ('dev', 'production')}
        css['dev'] = []
        css['production'] = []
        return css, saved

    def test_an_empty_css_list_renders_nothing_instead_of_raising(self):
        css, saved = self.zeroed_selectize_css()
        try:
            for tag in ('selectize.css', 'select.css'):
                # a TypeError here is the bug: `[] or [files]` used to become `[[]]`
                media = vendor(tag)
                self.assertNotIn('<link', str(media),
                                 msg='%s with an emptied list must contribute no stylesheet' % tag)
        finally:
            css.update(saved)


class VendorTreeTests(SimpleTestCase):

    def test_the_select2_vendor_directory_is_gone(self):
        self.assertFalse(os.path.isdir(os.path.join(STATIC_ROOT, 'vendor', 'select2')),
                         msg='66 files shipped in every release for nobody')


class NoSelect2LeftInSourceTests(SimpleTestCase):

    def test_no_source_file_mentions_select2(self):
        offenders = []
        for path in source_files():
            with open(path, encoding='utf-8', errors='replace') as handle:
                if 'select2' in handle.read().lower():
                    offenders.append(os.path.relpath(path, XADMIN_ROOT))
        self.assertEqual(sorted(offenders), [],
                         msg='select2 still mentioned in fork source: %s' % sorted(offenders))

    def test_the_build_pipeline_no_longer_declares_the_dependencies(self):
        with open(os.path.join(STATIC_ROOT, 'package.json'), encoding='utf-8') as handle:
            dependencies = json.load(handle).get('dependencies', {})
        for name in ('select2', 'bootstrap-multiselect'):
            self.assertTrue(name not in dependencies,
                            msg='%s has no gulp task and no consumer' % name)


class QuickFormTests(SimpleTestCase):

    def test_the_quick_form_only_reads_selectized_fields(self):
        source = read_static('js/xadmin.plugin.quick-form.js')
        self.assertTrue('hasClass("selectized")' in source,
                        msg='the + modal must still refresh a selectized multiple field')
        self.assertTrue('select2-multiple' not in source,
                        msg='the old marker class has no emitter since SEL-4')
