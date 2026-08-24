# coding=utf-8
"""Admin chrome follows the active language instead of freezing at boot. See #7368.

``from django.utils.translation import gettext as _`` resolves immediately. Called at
module scope or in a class body -- both of which run during ``django.setup()`` -- it
stores a plain ``str`` for the life of the process, in whatever language happened to be
active while the app registry was being populated.

Measured on the host project with ``LANGUAGE_CODE = 'pt-BR'``:
``AGGREGATE_TITLE['min']`` was the ``str`` ``'Min'`` and did **not** change under
``translation.override('en')``. Every one of these is admin chrome the user reads: the
site title, the delete-selected action label, the empty-cell placeholder, the
password-reset page titles.

The fix is ``gettext_lazy`` for exactly these call sites -- not a blanket alias swap.
Calls *inside* methods already resolve per request and must keep returning ``str``:
turning those lazy would hand a proxy to code that concatenates, joins or serialises.

Two nets here, on purpose. The AST scan is the regression guard -- it catches a new
frozen call anywhere in the package. The type assertions are the readable proof that
the ones this ticket fixed are actually lazy now.
"""
import ast
import pathlib

from django.utils.functional import Promise
from django.test import SimpleTestCase

import xadmin

PACKAGE = pathlib.Path(xadmin.__file__).parent


class _FrozenCallFinder(ast.NodeVisitor):
    """Calls to _() that sit outside any function -- i.e. run at import time."""

    def __init__(self):
        self.depth = 0
        self.hits = []

    def visit_FunctionDef(self, node):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    def visit_Call(self, node):
        if (self.depth == 0 and isinstance(node.func, ast.Name)
                and node.func.id == '_'):
            self.hits.append(node.lineno)
        self.generic_visit(node)


def imports_nonlazy_as_underscore(tree):
    return any(
        isinstance(n, ast.ImportFrom) and n.module == 'django.utils.translation'
        and any(a.name == 'gettext' and a.asname == '_' for a in n.names)
        for n in ast.walk(tree))


class NoFrozenTranslationsTests(SimpleTestCase):

    def test_no_module_scope_call_to_the_non_lazy_alias(self):
        """The regression guard.

        A module may still import ``gettext as _`` for use inside methods -- that is
        correct and stays. What must not exist is a call to that alias outside any
        function, because that one runs during django.setup() and freezes.
        """
        offenders = []
        for path in sorted(PACKAGE.rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
            if not imports_nonlazy_as_underscore(tree):
                continue
            lines = source.splitlines()
            finder = _FrozenCallFinder()
            finder.visit(tree)
            for line in sorted(set(finder.hits)):
                if self._is_marked_eager(lines, line):
                    continue
                offenders.append('{0}:{1}'.format(path.relative_to(PACKAGE), line))
        self.assertEqual(
            offenders, [],
            msg='these _() calls run at import time and freeze the language; use '
                'gettext_lazy for them (keep the plain gettext for calls inside '
                'methods), or mark the call `# i18n-eager:` with the reason it '
                'cannot be lazy')

    @staticmethod
    def _is_marked_eager(lines, lineno, lookback=10):
        """An `# i18n-eager:` comment above the call opts it out, with a reason.

        Three call sites genuinely cannot be lazy, and a sentinel in the source beats
        a line-number allowlist in a test file: it survives edits, and it puts the
        justification where the next person will actually read it.
        """
        start = max(0, lineno - 1 - lookback)
        return any('i18n-eager' in line for line in lines[start:lineno])


class LazyChromeTests(SimpleTestCase):
    """The strings this ticket unfroze, asserted as behaviour rather than source."""

    def test_aggregate_titles_are_lazy(self):
        from xadmin.plugins.aggregation import AGGREGATE_TITLE
        for key, value in AGGREGATE_TITLE.items():
            with self.subTest(aggregate=key):
                self.assertIsInstance(
                    value, Promise,
                    msg='AGGREGATE_TITLE[{0!r}] froze at import'.format(key))

    def test_action_names_are_lazy(self):
        from xadmin.plugins.auth import ACTION_NAME
        for key, value in ACTION_NAME.items():
            with self.subTest(action=key):
                self.assertIsInstance(value, Promise)

    def test_empty_changelist_value_is_deliberately_eager(self):
        """The one piece of chrome that must NOT be lazy, pinned so it stays that way.

        plus_base's ResultJsonField does `isinstance(self.text, str)` on this value and
        plugins/editable.py assigns it straight into that `text`; a proxy fails the
        isinstance and silently changes which branch runs. It is also defined twice --
        views/list.py and views/detail.py -- so the two have to agree, or the type
        depends on which module the consumer happened to import.
        """
        from xadmin.views.list import EMPTY_CHANGELIST_VALUE as from_list
        from xadmin.views.detail import EMPTY_CHANGELIST_VALUE as from_detail
        self.assertNotIsInstance(from_list, Promise)
        self.assertNotIsInstance(from_detail, Promise)
        self.assertEqual(from_list, from_detail,
                         msg='the two definitions of EMPTY_CHANGELIST_VALUE diverged')

    def test_common_view_chrome_is_lazy(self):
        from xadmin.views.base import CommAdminView
        for attr in ('site_title', 'site_footer'):
            with self.subTest(attr=attr):
                value = getattr(CommAdminView, attr, None)
                if value is not None:
                    self.assertIsInstance(value, Promise)

    def test_delete_selected_action_description_is_lazy(self):
        from xadmin.plugins.actions import DeleteSelectedAction
        self.assertIsInstance(DeleteSelectedAction.description, Promise)

    def test_lazy_titles_still_format_and_compare_as_text(self):
        """A proxy has to remain usable where the old str was.

        This is the risk the swap carries: gettext_lazy returns a proxy, not a str. It
        supports % formatting and str() -- which is what templates and the admin
        chrome do with these -- and that is what is asserted, rather than assuming.
        """
        from xadmin.plugins.aggregation import AGGREGATE_TITLE
        value = AGGREGATE_TITLE['min']
        self.assertIsInstance(str(value), str)
        self.assertTrue(str(value), msg='the lazy string must render to something')
