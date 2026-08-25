# coding=utf-8
"""Settings for the xadmin suite.

**There are deliberately no compatibility shims here.** That is the whole point of the
suite. Until #7093 the only way to boot xadmin on Django 5.x was for the *consumer* to
monkeypatch ``forms.ChoiceField._set_choices`` before importing xadmin, and to restore
``HttpRequest.is_ajax`` in middleware. Both are xadmin's own gaps, so both are fixed in
the package and this settings module stays clean -- if a shim ever has to come back
here, the fix regressed.

Note the MIDDLEWARE list below: it does **not** restore ``is_ajax``. The host project's
``RequestToolsMiddleware`` does, which is why the admin works in production today; the
suite runs without it on purpose so the views prove they no longer need it.
"""
SECRET_KEY = 'xadmin-suite-only-not-a-secret'
DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.admin',

    # xadmin's declared dependencies.
    'crispy_forms',
    'crispy_bootstrap4',
    'reversion',
    'import_export',
    'formtools',

    'xadmin',

    # Fixture app: gives the suite a reversion-enabled admin with an inline.
    'test_xadmin.fixtureapp',
]

MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

ROOT_URLCONF = 'test_xadmin.urls'

STATIC_URL = '/static/'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CRISPY_TEMPLATE_PACK = 'bootstrap4'
CRISPY_ALLOWED_TEMPLATE_PACKS = ('bootstrap4',)

# The fixture app ships no migrations; the runner builds its tables from the models.
MIGRATION_MODULES = {'xadmin_fixture': None}
