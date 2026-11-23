PLUGINS = (
	'actions',
	'filters',
	'export',
	'refresh',
	'details',
	'ajax',
	'relfield',
	'dependent',
	'inline',
	'quickform',
	'wizard',
	'images',
	'auth',
	'multiselect',
	'themes',
	'passwords',
	'sitemenu',
	'language',
	'sortablelist',
	'importexport',
	'xversion',
	'csrf_meta'
)


def register_builtin_plugins(site):
	from importlib import import_module
	from django.conf import settings

	plugins = PLUGINS + tuple(getattr(settings, 'XADMIN_INCLUDE_PLUGINS', ()))
	exclude_plugins = getattr(settings, 'XADMIN_EXCLUDE_PLUGINS', [])

	[import_module('xadmin.plugins.%s' % plugin) for plugin in plugins if plugin not in exclude_plugins]
