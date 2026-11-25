# coding=utf-8
"""Os plugins embutidos que o pacote traz e os que saíram dele. See #9054.

O #9054 tira do ``django-xadmin`` os módulos de plugin que o produto nunca carrega nem ativa. Três contratos:

* os 11 módulos que saem respondem ``ModuleNotFoundError`` ao import (AC-9054-4). O assert olha o ``name`` do erro: o
  ``comments`` importa ``django_comments`` (``plugins/comments.py:4-6``), que o ``requirements.txt`` do pacote não declara
  (neste dev ele está instalado, ``django-contrib-comments 2.2.0``), e sem essa checagem o caso de ``comments`` não provaria
  a remoção num ambiente só do pacote;
* os 22 plugins que ficam seguem carregados quando o xadmin sobe sem exclusões (AC-9054-5);
* os módulos e os nomes que o produto e os pacotes de extensão importam dos que ficam seguem expostos (AC-9054-6).

Os dois primeiros rodam num interpretador novo (como ``test_boot``): importar ``chart`` ou ``mobile`` num processo que já
subiu registraria plugin no site vivo e sujaria os outros testes da rodada. O terceiro importa no processo da suíte, que já
carrega esses módulos.

    python runtests.py test_xadmin.test_builtin_plugins
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_builtin_plugins'
"""
import json
from importlib import import_module

from django.test import SimpleTestCase

from test_xadmin.test_boot import run_in_fresh_interpreter

REMOVED_MODULES = ('bookmark', 'chart', 'portal', 'relate', 'topnav', 'layout', 'aggregation', 'quickfilter',
                   'editable', 'comments', 'mobile')

KEPT_PLUGINS = ('actions', 'filters', 'export', 'refresh', 'details', 'ajax', 'relfield', 'dependent', 'inline',
                'quickform', 'wizard', 'images', 'auth', 'multiselect', 'themes', 'passwords', 'sitemenu', 'language',
                'sortablelist', 'importexport', 'xversion', 'csrf_meta')

# (módulo, nomes que o produto e as extensões importam dele). A origem de cada grupo é um import em `plugins/system`
# (varredura de 23/11/2026): os caminhos sem repositório são relativos a `br.com.fabricadigital.plus.base/plus_base/`:
IMPORTED_NAMES = (
    # publique/xadmin_site/actions.py:10-11; xpublique/adminx/plugins/permissions.py:4-5; plus.ai.engine (adminx/ai_source.py:10);
    # plus.manager (plus_manager/publique_license/generator/plugins/server_bin.py:5, ACTION_CHECKBOX_NAME)
    ('actions', ('ACTION_CHECKBOX_NAME', 'BaseActionView', 'DeleteSelectedAction')),
    # xpublique/adminx/__init__.py:9; publique/adminx/__init__.py:16; publique/adminx/group.py:4;
    # xpublique/adminx/permission.py:2; publique/xadmin_site/admin/user.py:4
    ('auth', ('ChangeAccountPasswordView', 'ChangePasswordView', 'GroupAdmin', 'PermissionAdmin',
              'PermissionModelMultipleChoiceField')),
    # taguser/xadmin_site/batch_actions.py:10; publique/xadmin_site/actions.py:12
    ('batch', ('BATCH_CHECKBOX_NAME', 'BatchChangeAction')),
    # plus.hidra (plus_hidra/widgets.py:10)
    ('images', ('AdminImageWidget',)),
    # publique/utils/inline.py:2; xpublique/adminx/section_inline.py:7; xpublique/xadmin_site/xplugins/fancytree.py:5
    ('inline', ('AccInlineStyle', 'Inline', 'InlineFormset', 'InlineModelAdmin', 'InlineStyle', 'OneInlineStyle',
                'TabInlineStyle', 'TableInlineStyle', 'get_first_field', 'style_manager')),
    # xplugin_guardian/forms.py:3; xpublique/xadmin_site/widgets/permission.py:6
    ('multiselect', ('SelectMultipleTransfer',)),
    # publique/adminx/__init__.py:4
    ('passwords', ('ResetPasswordConfirmView',)),
    # plus.annotation (plus_annotation/adminx/annotations.py:12); plus.hidra.looper (plus_hidra_looper/plugins/toolbar/editable.py:7,
    # QUICKFORM_0_VAR)
    ('quickform', ('QUICKFORM_0_VAR', 'RelatedFieldWidgetWrapper')),
    # publique/adminx/__init__.py:17
    ('sitemenu', ('BUILDIN_STYLES',)),
    # xpublique/adminx/__init__.py:25
    ('sortablelist', ('SaveOrderView',)),
    # publique/xadmin_site/xplugins/newthemes.py:44
    ('themes', ('ThemePlugin',)),
    # publique/xadmin_site/xplugins/buttons.py:5 e outros 13 plugins do plus_base
    ('utils', ('get_context_dict',)),
    # taguser/adminx/__init__.py:20; taguser/xadmin_site/batch_actions.py:13; plus.elfinder (model_driver.py:51, views/__init__.py:14)
    ('xversion', ('BaseRevisionView', 'safe_revision_context')),
)

# Módulos importados como módulo, com os atributos que o `plus_base` lê por acesso ao módulo:
MODULE_ATTRIBUTES = (
    # publique/xadmin_site/xplugins/export.py:4,10,19,33,93-95
    ('export', ('ExportMenuPlugin', 'ExportPlugin')),
    ('importexport', ('ExportMenuPlugin',)),
    # publique/adminx/plugins/filters.py:5,11,58
    ('filters', ('FilterPlugin',)),
    # xpublique/adminx/__init__.py:24,153-155
    ('language', ('SetLangNavPlugin', 'SetLangView')),
    # publique/management/commands/xversion_revision_setup.py:4,23,27
    ('xversion', ('_register_model',)),
    # xpublique/adminx/plugins/permissions.py:4,8,52 e xpublique/adminx/section/__init__.py:394-399
    ('actions', ('ActionPlugin',)),
    ('inline', ('GenericInlineModelAdmin',)),
)


def _run(script, settings_extra=''):
    """Roda `script` num interpretador novo e devolve o JSON que ele imprime depois do marcador `RESULT`."""
    result = run_in_fresh_interpreter(script, settings_extra=settings_extra)
    if 'RESULT' not in result.stdout:
        raise AssertionError('the script did not finish. stderr:\n{0}'.format(result.stderr))
    return json.loads(result.stdout.split('RESULT', 1)[1].strip().splitlines()[0])


class RemovedModulesTests(SimpleTestCase):
    """Os 11 módulos que saem do pacote."""

    def test_importing_a_removed_plugin_module_raises_module_not_found_of_that_module(self):
        """AC-9054-4: importar `xadmin.plugins.<nome>` de um dos 11 módulos responde `ModuleNotFoundError` do próprio módulo."""
        outcome = _run('''
            import importlib, json
            out = {}
            for name in %r:
                try:
                    importlib.import_module('xadmin.plugins.' + name)
                    out[name] = 'imported'
                except ModuleNotFoundError as error:
                    out[name] = 'missing:' + str(error.name)
                except Exception as error:
                    out[name] = 'error:' + type(error).__name__
            print('RESULT', json.dumps(out))
        ''' % (REMOVED_MODULES,))
        for name in REMOVED_MODULES:
            with self.subTest(plugin=name):
                self.assertEqual(outcome.get(name), 'missing:xadmin.plugins.%s' % name,
                                 msg='the import must fail with the ModuleNotFoundError of the module itself, '
                                     'not succeed and not fail on another module')


class KeptPluginsTests(SimpleTestCase):
    """Os 22 plugins que o pacote mantém."""

    def test_the_builtin_plugins_that_stay_are_loaded_when_nothing_is_excluded(self):
        """AC-9054-5: o xadmin sobe sem exclusões e carrega os 22 plugins que ficam."""
        loaded = _run('''
            import json, sys
            names = sorted(name.rsplit('.', 1)[-1] for name in sys.modules if name.startswith('xadmin.plugins.'))
            print('RESULT', json.dumps(names))
        ''')
        # pré-condição positiva: o interpretador novo carregou plugins de verdade
        self.assertTrue(loaded, msg='fixture: the fresh interpreter must have loaded plugin modules')
        for name in KEPT_PLUGINS:
            with self.subTest(plugin=name):
                self.assertIn(name, loaded, msg='the plugin must be loaded when nothing is excluded')


class ImportedNamesTests(SimpleTestCase):
    """O que o produto e os pacotes de extensão importam dos módulos que ficam."""

    def test_the_names_the_product_and_the_extensions_import_are_still_exposed(self):
        """AC-9054-6: cada nome que o produto e as extensões importam de `xadmin.plugins.<módulo>` segue exposto."""
        cases = 0
        for module_name, names in IMPORTED_NAMES:
            module = import_module('xadmin.plugins.%s' % module_name)
            for name in names:
                with self.subTest(module=module_name, name=name):
                    cases += 1
                    self.assertTrue(hasattr(module, name), msg='xadmin.plugins.%s must expose %s' % (module_name, name))
        self.assertTrue(cases, msg='the loop must run')

    def test_the_modules_the_product_reads_by_module_access_expose_what_it_uses(self):
        """AC-9054-6: os módulos `export`, `filters`, `importexport`, `language` e `xversion` (e os atributos que o plus_base lê deles) seguem."""
        cases = 0
        for module_name, names in MODULE_ATTRIBUTES:
            module = import_module('xadmin.plugins.%s' % module_name)
            for name in names:
                with self.subTest(module=module_name, name=name):
                    cases += 1
                    self.assertTrue(hasattr(module, name), msg='xadmin.plugins.%s must expose %s' % (module_name, name))
        self.assertTrue(cases, msg='the loop must run')
