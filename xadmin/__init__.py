# Kept in step with setup.py's version by hand; test_packaging pins that they agree.
VERSION = (4, 1, 0)

from xadmin.sites import AdminSite, site


class Settings:
	pass


def autodiscover():
	"""
	Auto-discover INSTALLED_APPS admin.py modules and fail silently when
	not present. This forces an import on them to register any admin bits they
	may want.
	"""

	from importlib import import_module
	from django.conf import settings
	from django.utils.module_loading import module_has_submodule
	from django.apps import apps

	setattr(settings, 'CRISPY_TEMPLATE_PACK', 'bootstrap4')
	setattr(settings, 'CRISPY_CLASS_CONVERTERS', {
		"textinput": "textinput textInput form-control",
		"fileinput": "fileinput fileUpload form-control",
		"passwordinput": "textinput textInput form-control",
	})

	from xadmin.views import register_builtin_views
	register_builtin_views(site)

	# load xadmin settings from XADMIN_CONF module.
	# The default is a MODULE name. It used to be 'xadmin_conf.py', a file name, which
	# asks Python for a package `xadmin_conf` containing a submodule `py` -- so it could
	# never resolve, and every boot of every project that does not set the setting paid
	# a guaranteed ModuleNotFoundError. The bare `except Exception` is why nobody saw it.
	xadmin_conf = getattr(settings, 'XADMIN_CONF', 'xadmin_conf')
	try:
		conf_mod = import_module(xadmin_conf)
	except ModuleNotFoundError:
		# Absent is the normal case: the setting is optional.
		conf_mod = None

	if conf_mod:
		for key in dir(conf_mod):
			setting = getattr(conf_mod, key)
			try:
				if issubclass(setting, Settings):
					site.register_settings(setting.__name__, setting)
			except Exception:
				pass

	from xadmin.plugins import register_builtin_plugins
	register_builtin_plugins(site)

	for app_config in apps.get_app_configs():
		# skip deactivated plugin apps (models loaded, functionality disabled)
		if getattr(app_config, 'deactivated', False):
			continue

		before_import_registry = site.copy_registry()
		# Attempt to import the app's admin module.
		try:
			import_module('%s.adminx' % app_config.name)
		except ImportError:
			# Explicitly ImportError, not a bare except: the latter also swallowed
			# KeyboardInterrupt and SystemExit, and turned any genuine error inside a
			# valid adminx.py into "this app has no adminx" -- the admin then came up
			# missing models with no message anywhere.
			#
			# Reset the model registry to the state before the last import as
			# this import will have to reoccur on the next request and this
			# could raise NotRegistered and AlreadyRegistered exceptions
			# (see #8245).
			site.restore_registry(before_import_registry)

			# Decide whether to bubble up this error. If the app just
			# doesn't have an admin module, we can ignore the error
			# attempting to import it, otherwise we want it to bubble up.
			#
			# app_config.module is the package Django already imported in populate()
			# phase 1 (AppConfig.create -> import_module(entry)). Re-importing it here
			# cost 217 redundant calls and 0.045 s per boot.
			if module_has_submodule(app_config.module, 'adminx'):
				raise

	# initialize data conversion
	site.init()
