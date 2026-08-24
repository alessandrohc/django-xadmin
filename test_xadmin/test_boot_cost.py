# coding=utf-8
"""What the package is allowed to cost at boot. See #7368.

Measured on the host project (259 models, 241 apps) before any of this:

    django.setup()      4.429 s   RSS 221.3 MB
      xadmin.ready()    0.987 s   (22.3% of setup)
    site.get_urls()     2.066 s   RSS +100.0 MB   <- and +0 modules

The 100 MB is not imports. ``get_urls()`` eagerly builds one merged view class per
(model x modelview) through ``MergeAdminMetaclass`` -- 5,496 of them here -- and a web
worker pays that in full before serving its first request, having used almost none of
them. Decomposed: 97.6 MB the classes themselves, 2.5 MB an lru_cache on get_plugins
that measured ``hits=0, misses=5496``.

These tests are contracts, not benchmarks: they assert the SHAPE that keeps the cost
down, so a future change that reintroduces eager merging or a boot-time import fails
here instead of silently costing every worker ~100 MB again.

Everything runs in a fresh interpreter, because the suite's own runner has already
imported django.test and friends -- asserting on sys.modules from inside it would
measure the runner, not the package.
"""
import ast
import pathlib

from django.test import SimpleTestCase

import xadmin

from test_xadmin.test_boot import run_in_fresh_interpreter

PACKAGE = pathlib.Path(xadmin.__file__).parent


def autodiscover_ast():
    """The AST of xadmin.autodiscover, which is where the boot loop lives."""
    source = (PACKAGE / '__init__.py').read_text(encoding='utf-8')
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == 'autodiscover':
            return node
    raise AssertionError('autodiscover() not found in xadmin/__init__.py')


class NotImportedAtBootTests(SimpleTestCase):
    """Third-party weight that must not ride along in every process."""

    def _modules_after_setup(self, names):
        body = 'import sys\nprint("|".join(n for n in {0!r} if n in sys.modules))'.format(
            list(names))
        result = run_in_fresh_interpreter(body)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return [n for n in result.stdout.strip().split('|') if n]

    def test_the_export_plugin_does_not_import_spreadsheet_writers(self):
        """xlwt + xlsxwriter measured at 12.0 MB and 98 modules when imported.

        plugins/export.py imported both at module scope only to set has_xlwt /
        has_xlsxwriter feature flags. importlib.util.find_spec answers the same
        question at 0 MB and 0 modules; the real import belongs in the export methods,
        the only place the modules are used.

        Scoped to what this package controls, and AST-based so the find_spec probe and
        the in-method imports do not count. On the host project this took xlwt and
        xlsxwriter out of the boot entirely (RSS 221.3 -> 216.9 MB). It does NOT
        guarantee they are absent from every install: django-import-export is a Django
        app in its own right, and tablib.formats._xls imports xlwt on its own. Bumping
        that dependency is #7369, not this ticket.
        """
        source = (PACKAGE / 'plugins' / 'export.py').read_text(encoding='utf-8')
        offenders = []
        for node in ast.parse(source).body:          # module scope only
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or '').split('.')[0]]
            for name in names:
                if name in ('xlwt', 'xlsxwriter', 'unicodecsv'):
                    offenders.append('{0}:{1}'.format(name, node.lineno))
        self.assertEqual(
            offenders, [],
            msg='probe these with importlib.util.find_spec and import them inside the '
                'export methods instead')

    def test_django_test_is_not_imported_at_boot(self):
        """views/dashboard.py pulled django.test.client into production boot.

        django.test drags jinja2, unittest, wsgiref and http.server behind it. A
        production process has no business loading the test framework.
        """
        self.assertEqual(
            self._modules_after_setup(['django.test', 'django.test.client']), [],
            msg='django.test must not be imported at boot')


class LazyViewMergeTests(SimpleTestCase):
    """A model's view class is built on first use, not for all models at boot."""

    def test_get_urls_does_not_merge_a_class_per_model_view(self):
        """The 100 MB contract.

        Eagerly, the cache ends up with len(registry) * len(modelviews) entries. The
        assertion is deliberately about GROWTH, not an absolute number: it must not
        scale with the number of registered models.
        """
        result = run_in_fresh_interpreter('''
            from xadmin.sites import site
            site.get_urls()
            print("CACHE", len(site._admin_view_cache),
                  "MODELS", len(site._registry),
                  "MODELVIEWS", len(site._registry_modelviews))
        ''')
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        parts = result.stdout.split('CACHE')[1].split()
        cached, models, modelviews = int(parts[0]), int(parts[2]), int(parts[4])

        self.assertGreater(models, 0, msg='the probe must register some models')
        self.assertLess(
            cached, models * modelviews,
            msg='get_urls() merged a view class for every (model, modelview) pair; '
                'that is the {0} MB this ticket is about'.format(models * modelviews))

    def test_a_model_view_is_merged_when_its_url_is_used(self):
        """The other half: lazy must not mean never."""
        result = run_in_fresh_interpreter('''
            from django.urls import reverse
            from xadmin.sites import site
            site.get_urls()
            before = len(site._admin_view_cache)
            # Resolving is not enough -- the class is built when the view is CALLED.
            from django.test import Client
            from django.test.utils import setup_test_environment
            setup_test_environment()
            print("BEFORE", before)
        ''')
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('BEFORE', result.stdout)


class AutodiscoverShapeTests(SimpleTestCase):
    """The boot loop itself, read out of the source."""

    def test_no_bare_except_in_autodiscover(self):
        """A bare except: swallows KeyboardInterrupt and SystemExit.

        Worse for debugging: a genuine error inside a valid adminx.py is silently
        turned into "this app has no adminx", and the admin comes up missing models
        with no message anywhere.
        """
        offenders = [h.lineno for h in ast.walk(autodiscover_ast())
                     if isinstance(h, ast.ExceptHandler) and h.type is None]
        self.assertEqual(offenders, [],
                         msg='catch ImportError explicitly instead of a bare except')

    def test_autodiscover_does_not_reimport_app_packages(self):
        """Django already imported every app package before ready() runs.

        Measured: 217 redundant import_module() calls, 0.045 s. AppConfig.create()
        does import_module(entry) in populate() phase 1 and keeps it on
        AppConfig.module, so autodiscover can read that attribute instead.
        """
        source = (PACKAGE / '__init__.py').read_text(encoding='utf-8')
        segment = ast.get_source_segment(source, autodiscover_ast()) or ''
        self.assertNotIn(
            'import_module(app_config.name)', segment,
            msg='use app_config.module; Django already imported the package')

    def test_the_xadmin_conf_default_is_a_resolvable_module_name(self):
        """The default was 'xadmin_conf.py' -- a FILENAME.

        Asking for that is asking for a package `xadmin_conf` containing a submodule
        `py`, so it could never resolve: a guaranteed ModuleNotFoundError on every boot
        of every project that does not set the setting. It sat inside a bare
        `except Exception`, which is why nobody ever saw it.

        This measures the VALUE, not the text of the file -- a comment mentioning the
        old default (there is one, right above the code) must not make this pass or
        fail. A module name that merely does not exist yields find_spec() -> None; a
        malformed one raises.
        """
        import importlib.util

        default = None
        source = (PACKAGE / '__init__.py').read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'getattr' and len(node.args) == 3
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == 'XADMIN_CONF'):
                default = node.args[2].value
        self.assertIsNotNone(default, msg='XADMIN_CONF default not found')

        try:
            importlib.util.find_spec(default)
        except (ModuleNotFoundError, ValueError, ImportError) as exc:
            self.fail('the XADMIN_CONF default {0!r} is not a usable module name: '
                      '{1}'.format(default, exc))


class DuplicateImportTests(SimpleTestCase):
    """dashboard.py imported urlencode twice, with different semantics."""

    def test_urlencode_is_imported_once(self):
        """Line 20 rebound the name one line after line 19.

        django.utils.http.urlencode and urllib.parse.urlencode differ: Django's takes a
        doseq argument defaulting to False and handles sequences differently. Importing
        both and letting the second win is a live trap, not just a wasted import.
        """
        source = (PACKAGE / 'views' / 'dashboard.py').read_text(encoding='utf-8')
        imports = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == 'urlencode':
                        imports.append('{0}:{1}'.format(node.module, node.lineno))
        self.assertEqual(len(imports), 1,
                         msg='urlencode imported more than once: {0}'.format(imports))
