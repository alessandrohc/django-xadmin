# coding=utf-8
"""Boot contracts, exercised in a fresh interpreter.

These run in a subprocess on purpose. A boot failure inside ``django.setup()`` cannot
be observed from a suite that already booted -- by the time a test method runs, setup
has either succeeded or the whole run was an error. Spawning a clean interpreter is the
only way to assert on it and still have a readable suite.

It also lets the exclude-plugins contract be tested at all, since
``XADMIN_EXCLUDE_PLUGINS`` is only read once, during ``autodiscover()``.
"""
import os
import subprocess
import sys
import textwrap

from django.test import SimpleTestCase

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_in_fresh_interpreter(body, settings_extra=''):
    """Run `body` in a clean interpreter against a minimal xadmin settings.

    Returns the CompletedProcess. Settings are written inline rather than reusing
    ``test_xadmin.settings`` because several of these cases need to vary INSTALLED_APPS
    or XADMIN_EXCLUDE_PLUGINS, which only take effect before setup.
    """
    preamble = textwrap.dedent('''
        import sys
        sys.path.insert(0, {root!r})
        from django.conf import settings
        settings.configure(
            SECRET_KEY='boot-contract-only',
            DEBUG=False,
            ALLOWED_HOSTS=['testserver'],
            DATABASES={{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                                    'NAME': ':memory:'}}}},
            INSTALLED_APPS=[
                'django.contrib.auth', 'django.contrib.contenttypes',
                'django.contrib.sessions', 'django.contrib.messages',
                'django.contrib.staticfiles', 'django.contrib.admin',
                'crispy_forms', 'crispy_bootstrap4', 'reversion',
                'import_export', 'formtools', 'xadmin',
            ],
            MIDDLEWARE=[],
            TEMPLATES=[{{'BACKEND': 'django.template.backends.django.DjangoTemplates',
                        'APP_DIRS': True, 'DIRS': [],
                        'OPTIONS': {{'context_processors': []}}}}],
            STATIC_URL='/static/',
            USE_TZ=True,
            DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
            CRISPY_TEMPLATE_PACK='bootstrap4',
            CRISPY_ALLOWED_TEMPLATE_PACKS=('bootstrap4',),
            {settings_extra}
        )
        import django
        django.setup()
    ''').format(root=REPO_ROOT, settings_extra=settings_extra)
    # The body is concatenated at column zero. Interpolating it inside the template
    # and re-indenting afterwards breaks any body longer than one line, because only
    # the first line would lose its indent (IndentationError in the subprocess).
    script = preamble + '\n' + textwrap.dedent(body).strip() + '\n'
    env = dict(os.environ)
    env.pop('DJANGO_SETTINGS_MODULE', None)
    return subprocess.run([sys.executable, '-c', script],
                          capture_output=True, text=True, timeout=300)


class SetupContractTests(SimpleTestCase):
    """``django.setup()`` finishes with no consumer-applied shim."""

    def test_django_setup_completes_without_shims(self):
        """Blocker number one of #7093 dies here.

        Before the fix, on Django >= 5.0, this exits with an AttributeError raised from
        ``xadmin/views/dashboard.py`` inside ``autodiscover()``.
        """
        result = run_in_fresh_interpreter("print('SETUP-OK')")
        self.assertIn('SETUP-OK', result.stdout,
                      msg='django.setup() must finish with xadmin installed and no '
                          'consumer-side shim. stderr:\n{0}'.format(result.stderr))

    def test_site_urls_build(self):
        """The Python 3.14 blocker dies here.

        ``site.urls`` builds the routes by calling ``self.path(...)`` on partials held
        as class attributes. On 3.14 that raises TypeError, and because it happens while
        importing the urlconf it is a boot failure.
        """
        result = run_in_fresh_interpreter('''
            from xadmin.sites import site
            patterns, name, app_name = site.urls
            print('URLS-OK', len(patterns))
        ''')
        self.assertEqual(result.returncode, 0,
                         msg='site.urls must build. stderr:\n{0}'.format(result.stderr))
        self.assertIn('URLS-OK', result.stdout,
                      msg='stderr:\n{0}'.format(result.stderr))
        # The count is the point: an empty urlconf would still print the marker.
        count = int(result.stdout.split('URLS-OK')[1].split()[0])
        self.assertGreater(count, 1, msg='site.urls built but produced no routes')


class ExcludePluginsContractTests(SimpleTestCase):
    """``XADMIN_EXCLUDE_PLUGINS`` is a documented option and has to work.

    Found during #7093 while pricing each builtin plugin: excluding ``xversion`` brings
    the whole boot down. ``apps.py`` imports ``xadmin.plugins.xversion`` unconditionally
    in ``ready()``, **after** ``site.init()`` has sealed the site, and the module calls
    ``site.register(...)`` at module level -- so the late registration raises
    ImproperlyConfigured.
    """

    def test_excluding_a_plugin_does_not_break_boot(self):
        """Control case: excluding an ordinary plugin must be harmless."""
        result = run_in_fresh_interpreter(
            "print('SETUP-OK')",
            settings_extra="XADMIN_EXCLUDE_PLUGINS=['chart'],")
        self.assertIn('SETUP-OK', result.stdout,
                      msg='stderr:\n{0}'.format(result.stderr))

    def test_excluding_xversion_does_not_break_boot(self):
        """The defect itself."""
        result = run_in_fresh_interpreter(
            "print('SETUP-OK')",
            settings_extra="XADMIN_EXCLUDE_PLUGINS=['xversion'],")
        self.assertIn('SETUP-OK', result.stdout,
                      msg='excluding xversion must not raise ImproperlyConfigured '
                          'from apps.ready(). stderr:\n{0}'.format(result.stderr))
