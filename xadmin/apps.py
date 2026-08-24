import sys

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

import xadmin


class XAdminConfig(AppConfig):
	"""Simple AppConfig which does not do automatic discovery."""

	name = 'xadmin'
	verbose_name = _("Administration")

	def ready(self):
		self.module.autodiscover()
		setattr(xadmin, 'site', xadmin.site)
		# Eagerly register all models that have reversion_enable=True with
		# django-reversion so that history is captured before the first request,
		# Celery task or management command touches them.
		#
		# Only when the xversion plugin was actually loaded. XADMIN_EXCLUDE_PLUGINS
		# may have kept it out, and importing it here would run its module-level
		# site.register() *after* autodiscover() called site.init() and sealed the
		# registry -- which raises ImproperlyConfigured and takes the whole boot down.
		#
		# sys.modules is the only honest way to ask: plugins register themselves as a
		# side effect of being imported, and the registry is keyed by view class, so
		# nothing maps back to a plugin name. Re-reading XADMIN_EXCLUDE_PLUGINS here
		# would duplicate the loader's logic and still miss XADMIN_INCLUDE_PLUGINS.
		#
		# This cannot move into xversion.py itself, where it would belong: it walks
		# site._registry, which autodiscover() only fills later, from the adminx
		# modules. Hence the call sits here, after discovery.
		xversion = sys.modules.get('xadmin.plugins.xversion')
		if xversion is not None:
			xversion.register_models()
