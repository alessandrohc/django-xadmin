#!/usr/bin/env python
# coding=utf-8
"""Entry point for the xadmin suite, on Django's native test runner.

The legacy ``tests/`` harness cannot be used on modern Django: ``tests/runtests.py``
calls ``run_tests(..., extra_tests=[])`` and that argument was removed in Django 5.0.
This runner replaces it.

The point of this suite is that it applies **no compatibility shims**. Consumers of the
fork (the host project, and every xadmin plugin package) have been carrying shims in
their own test settings for xadmin's benefit -- see #7093. Those shims belong here, in
the package, as real fixes; the suite is what proves they landed.

    python runtests.py                          # whole suite
    python runtests.py test_xadmin.test_compat  # a single module
    python runtests.py -v 3 --failfast          # runner flags

The suite is meant to run on the whole support matrix, Django 4.2 through 5.2 on
Python 3.10 through 3.14. ``matrix.sh`` drives that; this runs one cell.
"""
import argparse
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the xadmin suite.")
    parser.add_argument('labels', nargs='*', default=None,
                        help="test labels (default: test_xadmin)")
    parser.add_argument('-v', '--verbosity', type=int, default=2, choices=[0, 1, 2, 3])
    parser.add_argument('--failfast', action='store_true')
    parser.add_argument('--keepdb', action='store_true')
    options = parser.parse_args(argv)

    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)
    # Assign, never setdefault: an inherited DJANGO_SETTINGS_MODULE would silently win
    # and the suite would measure a foreign settings module -- defeating the entire
    # point of test_xadmin/settings.py, which is that it applies no shims.
    os.environ['DJANGO_SETTINGS_MODULE'] = 'test_xadmin.settings'

    import django
    from django.conf import settings
    from django.test.utils import get_runner

    django.setup()

    runner_class = get_runner(settings)
    runner = runner_class(verbosity=options.verbosity,
                          interactive=False,
                          failfast=options.failfast,
                          keepdb=options.keepdb)
    failures = runner.run_tests(options.labels or ['test_xadmin'])
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
