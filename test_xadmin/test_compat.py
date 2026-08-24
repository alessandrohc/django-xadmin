# coding=utf-8
"""Removed Django and Python APIs, read out of the shipped source.

These tests read the source rather than exercising it, on purpose. A dependency on a
removed API that only lives on an AJAX branch, or in the reversion plugin, would
otherwise sit there for years and only surface as a 500 in production -- which is
exactly how xadmin arrived at #7093 carrying three separate boot blockers at once.

They also run on Django 4.2, where the package still works. That is deliberate: it
means the fork stops depending on a removed API *before* the project takes the 5.2
jump, instead of during it.
"""
import ast
import functools
import pathlib
import warnings

from django.test import SimpleTestCase

import xadmin

PACKAGE = pathlib.Path(xadmin.__file__).parent


def sources():
    """Every .py module shipped inside the xadmin package."""
    for path in sorted(PACKAGE.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        yield path, path.read_text(encoding='utf-8')


def rel(path):
    return str(path.relative_to(PACKAGE))


def calls_to(attr_name):
    """`<something>.<attr_name>(...)` calls, found through the AST rather than text.

    Walking the AST instead of grepping keeps a docstring or a comment naming the
    method from counting as a use -- and this module names all of them.
    """
    hits = []
    for path, source in sources():
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == attr_name):
                hits.append('{0}:{1}'.format(rel(path), node.lineno))
    return hits


class RemovedInDjango50Tests(SimpleTestCase):
    """What 5.0 took away. These stop the boot or the request, none are cosmetic."""

    def test_no_reliance_on_the_private_choicefield_choices_setter(self):
        """``ChoiceField._get_choices``/``_set_choices`` went in Django 5.0.

        This is blocker number one of #7093: ``xadmin/views/dashboard.py`` builds
        ``property(_get_choices, forms.ChoiceField._set_choices)`` at class definition
        time, so ``import xadmin.views`` raises inside ``autodiscover()`` and
        ``django.setup()`` never finishes. That is a boot failure, not a runtime one.

        The replacement reads the setter off Django's own property
        (``forms.ChoiceField.choices.fset``), which exists on 4.2 and on 5.2, so no
        version predicate is needed.
        """
        offenders = ['{0}:{1}'.format(rel(path), n)
                     for path, source in sources()
                     for n, line in enumerate(source.splitlines(), 1)
                     if 'ChoiceField._set_choices' in line
                     or 'ChoiceField._get_choices' in line]
        self.assertEqual(offenders, [],
                         msg='xadmin must not reuse the private ChoiceField choices '
                             'pair; it was removed in Django 5.0')


class RemovedInDjango51Tests(SimpleTestCase):
    """What 5.1 took away."""

    def test_nothing_calls_the_removed_foreignobjectrel_is_hidden(self):
        """``ForeignObjectRel.is_hidden()`` went in Django 5.1.

        Measured: present on 4.2.30, absent on 5.2.17. The ``.hidden`` property
        exists on BOTH versions, so the swap needs no version predicate.

        The search is AST-based and counts **calls** only. That matters here: there is
        a ``bound_field.is_hidden`` in ``xadmin/helpers.py`` which is a different API
        -- the ``BoundField`` property, alive and well on 5.2 -- and a textual search
        would confuse the two.
        """
        self.assertEqual(calls_to('is_hidden'), [],
                         msg='ForeignObjectRel.is_hidden() was removed in Django 5.1; '
                             'use the .hidden property, which exists on 4.2 too')


class RemovedInDjango40Tests(SimpleTestCase):
    """What 4.0 and 4.1 took away and the fork still carries."""

    def test_nothing_calls_request_is_ajax(self):
        """``HttpRequest.is_ajax()`` went in Django 4.0.

        xadmin calls it in five places and one of them -- ``plugins/ajax.py`` -- runs
        on **every** admin view through the plugin manager. Measured on Django 5.2.17:
        without middleware putting the method back, 6 of 7 admin URLs answer 500.

        That the admin works in production today is an accident of hosting: plus_base's
        ``RequestToolsMiddleware`` restores the method. Removing that coupling is what
        this test pins -- the right predicate is the header, and it works on any
        version.
        """
        self.assertEqual(calls_to('is_ajax'), [],
                         msg='HttpRequest.is_ajax() was removed in Django 4.0; test '
                             "request.headers.get('x-requested-with') instead")

    def test_no_default_app_config(self):
        """``default_app_config`` stopped being read in Django 4.1.

        It breaks nothing -- it is a module attribute nobody consults any more -- but
        it points at the wrong AppConfig for anyone who believes it.
        """
        offenders = [rel(path) for path, source in sources()
                     if 'default_app_config' in source]
        self.assertEqual(offenders, [],
                         msg='default_app_config is dead since Django 4.1')

    def test_no_removed_translation_or_encoding_helpers(self):
        """ugettext*/smart_text/force_text went in 4.0. Regression net."""
        removed = ('ugettext', 'ugettext_lazy', 'ungettext', 'ungettext_lazy',
                   'smart_text', 'force_text')
        offenders = [(rel(path), name) for path, source in sources()
                     for name in removed if name in source]
        self.assertEqual(offenders, [])

    def test_urls_are_not_built_with_the_removed_conf_helper(self):
        """``django.conf.urls.url`` went in 4.0. Regression net.

        AST-based rather than textual: the docstring of ``AdminSite.admin_view`` used
        to show an example importing ``url`` from ``django.conf.urls``, which is stale
        documentation rather than a use. That example was corrected alongside, but the
        test has to measure real imports either way.
        """
        offenders = []
        for path, source in sources():
            for node in ast.walk(ast.parse(source)):
                if (isinstance(node, ast.ImportFrom)
                        and node.module == 'django.conf.urls'
                        and any(a.name == 'url' for a in node.names)):
                    offenders.append('{0}:{1}'.format(rel(path), node.lineno))
        self.assertEqual(offenders, [])

    def test_no_signal_declares_providing_args(self):
        """``Signal(providing_args=...)`` went in 4.0. Regression net."""
        offenders = [rel(path) for path, source in sources()
                     if 'providing_args' in source]
        self.assertEqual(offenders, [])


class RemovedModelMetaTests(SimpleTestCase):
    """``index_together`` went in Django 5.1. Regression net."""

    def test_no_removed_model_meta_option(self):
        offenders = [rel(path) for path, source in sources()
                     if 'index_together' in source]
        self.assertEqual(offenders, [])


class PythonCompatTests(SimpleTestCase):
    """Python removals, which break the whole import rather than one code path."""

    def test_no_invalid_escape_sequences(self):
        """A SyntaxWarning on 3.12 that becomes a SyntaxError in a future release.

        Measured on Python 3.14.4: ``xadmin/plugins/export.py`` emitted
        ``SyntaxWarning: "\\," is an invalid escape sequence``. The compile() below is
        what the interpreter does on import, so this measures rather than assumes.

        The category is NOT stable across the matrix: 3.12 promoted these to
        SyntaxWarning, but 3.10 and 3.11 still report DeprecationWarning. Filtering on
        SyntaxWarning alone made this test vacuous on both 3.10 cells -- including
        py3.10 + Django 4.2.30, which is production and the likeliest cell to be the
        only one wired into CI. Hence the match is on the message, across both
        categories, and ``test_the_escape_probe_is_detected`` proves the net is live on
        whatever interpreter is running.
        """
        self.assertEqual(self._invalid_escapes_in(sources()), [])

    @staticmethod
    def _invalid_escapes_in(pairs):
        offenders = []
        for path, source in pairs:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                compile(source, str(path), 'exec')
            for item in caught:
                if (issubclass(item.category, (SyntaxWarning, DeprecationWarning))
                        and 'invalid escape sequence' in str(item.message)):
                    offenders.append('{0}: {1}'.format(
                        path if isinstance(path, str) else rel(path), item.message))
        return offenders

    def test_the_escape_probe_is_detected(self):
        """The net must be live on THIS interpreter, not merely green.

        A green result from the test above is meaningless if the running Python reports
        invalid escapes under a category the filter does not collect. This feeds it a
        known-bad snippet and demands a hit, so the guard fails loudly on any
        interpreter where it cannot measure.
        """
        probe = [('<probe>', "value = '\\,'\n")]
        self.assertNotEqual(
            self._invalid_escapes_in(probe), [],
            msg='the invalid-escape detector is inert on this interpreter; widen the '
                'warning categories it collects')

    def test_no_stdlib_modules_removed_in_python_3_12(self):
        """PEP 594/632. One of these and the package will not even import."""
        removed = ('distutils', 'telnetlib', 'imp', 'pipes', 'cgi',
                   'asynchat', 'asyncore', 'smtpd', 'nntplib')
        offenders = []
        for path, source in sources():
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    names = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or '').split('.')[0]]
                else:
                    continue
                for name in names:
                    if name in removed:
                        offenders.append('{0}:{1} {2}'.format(rel(path), node.lineno, name))
        self.assertEqual(offenders, [])

    def test_no_removed_unittest_aliases(self):
        """assertEquals and friends went in 3.12. Regression net."""
        removed = ('assertEquals', 'assertNotEquals', 'assertAlmostEquals',
                   'assertRegexpMatches', 'assertRaisesRegexp', 'failUnless',
                   'failIf')
        offenders = [(rel(path), name) for path, source in sources()
                     for name in removed if name in source]
        self.assertEqual(offenders, [])


class UrlHelperDescriptorTests(SimpleTestCase):
    """``functools.partial`` became a method descriptor in Python 3.14.

    ``AdminRoute.path``, ``AdminUrl.path`` and ``AdminPath.path`` hold ``re_path``/
    ``path`` -- both ``functools.partial`` objects -- as **class attributes**. From
    3.14 on ``partial`` has ``__get__``, so ``self.path(route, view)`` passes the
    instance as the first positional argument and Django's ``_path()`` raises
    ``TypeError: _path() got multiple values for argument 'kwargs'``. Measured on
    3.14.4: it fails while building ``site.urls``, so it is a boot failure.

    ``staticmethod()`` is the whole fix and it is version-neutral -- on 3.10 a
    ``partial`` has no ``__get__`` and ``staticmethod(partial)`` behaves exactly like
    the bare partial. That is why the declaration test runs on the whole matrix while
    the behaviour only diverges on 3.14.
    """

    def test_url_helpers_are_declared_as_staticmethod(self):
        from xadmin import sites

        offenders = []
        for klass in (sites.AdminRoute, sites.AdminUrl, sites.AdminPath):
            helper = klass.__dict__.get('path')
            if isinstance(helper, functools.partial):
                offenders.append(klass.__name__)
        self.assertEqual(offenders, [],
                         msg='wrap the partial in staticmethod() so Python 3.14 does '
                             'not bind it as a method')
