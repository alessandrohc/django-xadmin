# coding=utf-8
"""What the package declares about itself.

A code fix that leaves ``requirements.txt`` pinned at ``django<5`` is not installable
on the target: pip refuses the resolution before any of the code runs. This module
pins the declaration alongside the behaviour.
"""
import os
import pathlib
import re
import sys
import types

import django
from django.test import SimpleTestCase

import xadmin

REPO_ROOT = pathlib.Path(xadmin.__file__).parent.parent
REQUIREMENTS = REPO_ROOT / 'requirements.txt'
SETUP_PY = REPO_ROOT / 'setup.py'
MANIFEST_IN = REPO_ROOT / 'MANIFEST.in'


def django_requirement_line():
    """The Django requirement, matched exactly rather than by prefix.

    ``startswith('django')`` also matches django-crispy-forms, django-reversion and
    every other hyphenated sibling; excluding hyphens specifically would still match a
    hypothetical non-hyphenated ``djangorestframework``. Anchoring on the project name
    followed by a version operator, an extras bracket, a marker or end-of-line is the
    only spelling that cannot pick the wrong line.
    """
    for line in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if re.match(r'^django\s*(\[|[<>=!~;]|$)', line, re.IGNORECASE):
            return line
    return None


def bound_from(line, operator):
    match = re.search(re.escape(operator) + r'\s*([0-9]+(?:\.[0-9]+)*)', line or '')
    return tuple(int(part) for part in match.group(1).split('.')) if match else None


def capture_setup_kwargs():
    """Run setup.py and return the kwargs it passes to setup(), without installing.

    A stub module rather than mock.patch('setuptools.setup'): patching would have to
    import the real setuptools, and a Python 3.12+ venv does not seed it. What is being
    measured is the value setup.py PASSES, so the real package is not needed.
    """
    captured = {}
    stub = types.ModuleType('setuptools')
    stub.setup = lambda **kwargs: captured.update(kwargs)
    stub.find_packages = lambda *a, **kw: []

    source = SETUP_PY.read_text(encoding='utf-8')
    namespace = {'__file__': str(SETUP_PY), '__name__': 'setup'}
    previous = sys.modules.get('setuptools')
    sys.modules['setuptools'] = stub
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)                  # setup.py reads requirements.txt relatively
    try:
        exec(compile(source, str(SETUP_PY), 'exec'), namespace)
    finally:
        os.chdir(cwd)
        if previous is None:
            sys.modules.pop('setuptools', None)
        else:
            sys.modules['setuptools'] = previous
    return captured


class DeclaredDependencyTests(SimpleTestCase):
    """The declared range has to cover the matrix the suite runs on."""

    def test_requirements_file_exists(self):
        self.assertTrue(REQUIREMENTS.is_file(),
                        msg='setup.py reads install_requires from requirements.txt')

    def test_django_requirement_admits_the_running_version(self):
        """The test that closes the hole: the pin must admit the running Django.

        It runs on every matrix cell, so on the 5.2 cell it is red for as long as the
        file says ``django>=3,<5`` -- which is exactly today's state.
        """
        line = django_requirement_line()
        self.assertIsNotNone(line, msg='requirements.txt must pin Django')

        bound = bound_from(line, '<')
        self.assertIsNotNone(
            bound, msg='the Django requirement should carry an upper bound: ' + line)

        running = django.VERSION[:len(bound)]
        self.assertLess(
            running, bound,
            msg='requirements.txt says "{0}" but the suite is running Django {1}; '
                'pip would refuse this install'.format(line, django.get_version()))

    def test_django_requirement_still_admits_the_production_version(self):
        """The project runs 4.2.30 today. Raising the ceiling must not raise the floor."""
        bound = bound_from(django_requirement_line(), '>=')
        self.assertIsNotNone(bound, msg='the Django requirement needs a lower bound')
        # Compare the SERIES only. Comparing the full tuple would make a legitimate
        # patch-level floor such as >=4.2.30 -- the exact version production runs, and
        # the one that carries the 4.2 security fixes -- fail this test.
        self.assertLessEqual(bound[:2], (4, 2),
                             msg='production runs Django 4.2.x; the floor must not '
                                 'rise above the 4.2 series')


    def test_declared_version_matches_the_package_version(self):
        """setup.py's version and xadmin.VERSION must not drift apart.

        They did: the distribution was at 3.6.25 while ``xadmin.VERSION`` still said
        (0, 6, 0), a number from long before this fork existed. Anything reading the
        runtime attribute got a wrong answer.
        """
        captured = capture_setup_kwargs()
        declared = captured.get('version')
        runtime = '.'.join(str(part) for part in xadmin.VERSION)
        self.assertEqual(
            declared, runtime,
            msg='setup.py says {0!r} but xadmin.VERSION says {1!r}'.format(
                declared, runtime))


class SuiteLocationTests(SimpleTestCase):
    """The suite lives outside the package and must not travel inside the wheel.

    The sibling ``xadmin-notification`` shipped a ``tests.py`` inside its wheel and a
    consumer's runner collected it, failing with an AttributeError from a removed
    unittest alias. Here the suite is a sibling package, and this test pins that.
    """

    def test_no_test_module_ships_inside_the_package(self):
        offenders = [str(p.relative_to(REPO_ROOT))
                     for p in (pathlib.Path(xadmin.__file__).parent).rglob('test*.py')
                     if '__pycache__' not in p.parts]
        self.assertEqual(offenders, [],
                         msg='tests must not ship inside the xadmin package')

    def test_setup_py_does_not_package_the_suite(self):
        """Measure the value passed to setup(), not the spelling of the file.

        Asserting the literal string is absent would stay green through the very change
        it exists to catch: swapping the hand-written list for
        ``find_packages(exclude=[...])`` ships the suite inside the wheel without the
        string ``test_xadmin`` ever appearing in setup.py.
        """
        packages = capture_setup_kwargs().get('packages')
        self.assertIsNotNone(packages, msg='setup.py must declare packages explicitly')
        offenders = [name for name in packages if name.split('.')[0] == 'test_xadmin']
        self.assertEqual(offenders, [],
                         msg='the suite must not be packaged into the wheel')

    def test_sdist_carries_the_requirements_file(self):
        """setup.py reads requirements.txt at build time, so the sdist needs it.

        Without it the sdist cannot be installed at all: ``load_requirements()`` opens
        the file while setup() runs, and pip runs setup() from the unpacked sdist.
        """
        self.assertTrue(MANIFEST_IN.is_file(), msg='MANIFEST.in must exist')
        manifest = MANIFEST_IN.read_text(encoding='utf-8')
        self.assertIn(
            'requirements.txt', manifest,
            msg='MANIFEST.in must include requirements.txt or the sdist is uninstallable')
