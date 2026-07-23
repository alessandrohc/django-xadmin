# coding:utf-8
"""Exposes the CSRF token in the admin <head> as <meta name="csrf-token">.

Needed when the csrftoken cookie is not readable by JS (CSRF_COOKIE_HTTPONLY) or
does not exist (CSRF_USE_SESSIONS): admin AJAX that only sends the token via the
X-CSRFToken header (read from the cookie) would otherwise fail CSRF. The token
reader falls back to this meta. Generic plugin (no project-specific coupling)."""
from django.conf import settings
from django.middleware.csrf import get_token
from django.utils.html import format_html

from xadmin.sites import site
from xadmin.views import BaseAdminPlugin, CommAdminView


class CsrfTokenMetaPlugin(BaseAdminPlugin):
    """Injects <meta name="csrf-token"> into the admin <head> (extrahead block)."""

    def init_request(self, *args, **kwargs):
        # Only needed when the cookie is not JS-readable (httponly) or absent
        # (use_sessions). No CACHE_UNIFORM gate here: admin is authenticated and
        # not served from the public cookieless page cache.
        return bool(settings.CSRF_COOKIE_HTTPONLY or settings.CSRF_USE_SESSIONS)

    def block_extrahead(self, context, nodes):
        # get_token yields the masked token and marks the csrftoken cookie for
        # sending; format_html escapes the value into the attribute.
        nodes.append(format_html('<meta name="csrf-token" content="{}">', get_token(self.request)))


site.register_plugin(CsrfTokenMetaPlugin, CommAdminView)
