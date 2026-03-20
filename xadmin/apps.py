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
		from xadmin.plugins.xversion import register_models
		register_models()
