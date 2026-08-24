# coding=utf-8
"""``?_fields=`` cannot reach past what the admin declares. See #7369.

``AjaxListPlugin.get_list_display`` returned the caller's ``?_fields=`` list verbatim.
It is a ``filter_hook``, so that list *replaces* the view's ``list_display`` and the
result cells are then built for whatever was asked. Reproduced on the host project:

    GET /admin/xadmin/plus_manager/user/?_format=json&_ajax=1&_fields=password
    -> 200 {"objects": [{"password": "argon2$argon2id$v=19$m=102400,t=2,p=8$VHB5..."}]}

while that admin's list_display is ('unicode_adminx', 'email', 'groups_str',
'is_active', 'last_login') -- ``password`` is nowhere near it.

Four things made it worse than it reads:

- ``?_ajax=1`` in the query string is enough; the XHR header is not required, so the URL
  works pasted into a browser.
- ``ListAdminView.init_request`` only calls ``has_view_permission()``, which is ``view``
  OR ``change``. A read-only auditing role could dump every password hash, and
  ``is_superuser``, and any other column of any model.
- The ``ajax`` plugin is active here (XADMIN_EXCLUDE_PLUGINS drops bookmark, topnav,
  chart, relate, themes, export and portal -- not this one).
- An unknown field raised an unhandled AttributeError from
  ``views/list.py`` ``get_ordering_field`` -> HTTP 500, so the same parameter was also a
  one-GET denial of service for anyone with ``view``.

Nothing consumes ``?_fields=``: grepping the whole fork including its JavaScript, and
the project's plugins, finds only the read in ``plugins/ajax.py``. So intersecting with
the declared ``list_display`` closes both holes without a client to break.
"""
import json

from django.contrib.auth.models import User
from django.test import TestCase

from test_xadmin.fixtureapp.models import Author

LIST_URL = '/xadmin/xadmin_fixture/author/'


class FieldSelectionTests(TestCase):
    """End to end, through the real plugin manager and the real view."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'ajax-admin', 'ajax-admin@example.com', 'not-a-real-password')
        Author.objects.create(name='visible name', secret='top-secret-value')

    def setUp(self):
        self.client.force_login(self.user)

    def _json(self, query):
        response = self.client.get(LIST_URL + query)
        self.assertEqual(response.status_code, 200,
                         msg='the changelist must answer, not 500')
        self.assertIn('application/json', response['Content-Type'])
        return json.loads(response.content)

    def test_a_field_outside_list_display_is_not_served(self):
        """The leak itself, in miniature.

        AuthorAdmin.list_display is ('name',). `secret` is a real model field the admin
        deliberately does not expose -- exactly the shape of User.password.
        """
        payload = self._json('?_format=json&_ajax=1&_fields=secret')

        self.assertNotIn('secret', payload.get('headers', {}),
                         msg='?_fields= must not reach past list_display')
        for row in payload.get('objects', []):
            self.assertNotIn('secret', row,
                             msg='the undeclared field leaked into the payload')
        self.assertNotIn('top-secret-value', response_text(payload),
                         msg='the undeclared value leaked into the payload')

    def test_the_xhr_header_is_not_a_barrier(self):
        """`?_ajax=1` alone selects the JSON branch, so the header proves nothing."""
        payload = self._json('?_format=json&_ajax=1&_fields=secret')
        self.assertNotIn('top-secret-value', response_text(payload))

    def test_a_declared_field_still_works(self):
        """The legitimate use -- asking for a subset of the declared columns -- survives."""
        payload = self._json('?_format=json&_ajax=1&_fields=name')
        self.assertEqual(list(payload.get('headers', {})), ['name'])

    def test_an_unknown_field_does_not_500(self):
        """The second defect: an unknown name reached getattr(self.model, ...).

        views/list.py get_ordering_field does `attr = getattr(self.model, field_name)`,
        so `?_fields=nope` raised AttributeError and the page returned 500 -- a denial
        of service available to anyone holding `view`.
        """
        payload = self._json('?_format=json&_ajax=1&_fields=nope')
        self.assertNotIn('nope', payload.get('headers', {}))

    def test_no_fields_parameter_serves_the_declared_columns(self):
        payload = self._json('?_format=json&_ajax=1')
        self.assertEqual(list(payload.get('headers', {})), ['name'])


def response_text(payload):
    return json.dumps(payload, default=str)


class GetListDisplayUnitTests(TestCase):
    """The hook in isolation, so a failure points straight at the filter."""

    def _plugin(self, query):
        from django.test import RequestFactory

        from xadmin.plugins.ajax import AjaxListPlugin

        class _View:
            def __init__(self, request):
                self.request = request
                self.admin_site = None
                self.user = None
                self.args = ()
                self.kwargs = {}

        request = RequestFactory().get('/' + query)
        return AjaxListPlugin(_View(request))

    def test_requested_fields_are_intersected_with_the_declared_ones(self):
        plugin = self._plugin('?_fields=name,secret')
        self.assertEqual(plugin.get_list_display(['name']), ['name'])

    def test_unknown_fields_are_dropped(self):
        plugin = self._plugin('?_fields=nope')
        self.assertEqual(plugin.get_list_display(['name']), ['name'],
                         msg='an empty intersection must fall back to list_display '
                             'rather than serving an empty changelist')

    def test_an_empty_parameter_is_a_no_op(self):
        plugin = self._plugin('?_fields=')
        self.assertEqual(plugin.get_list_display(['name', 'other']), ['name', 'other'])

    def test_the_declared_order_is_not_a_way_in(self):
        """Asking for a declared field twice, or in another order, changes nothing
        about which fields are reachable."""
        plugin = self._plugin('?_fields=secret,name,secret')
        self.assertEqual(plugin.get_list_display(['name']), ['name'])
