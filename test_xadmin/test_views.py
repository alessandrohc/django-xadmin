# coding=utf-8
"""The admin actually serves, with no host-supplied compatibility middleware.

``test_xadmin/settings.py`` installs no middleware that restores ``is_ajax``. That is
the contract these tests pin: xadmin must not need the host project to patch
``HttpRequest`` for the admin to answer.

Measured on Django 5.2.17 before the fix -- 6 of these 7 URLs raised
``AttributeError: 'WSGIRequest' object has no attribute 'is_ajax'``. The same is true
on Django 4.2, because the method went in 4.0; production only works because
``plus_base`` puts it back in middleware.
"""
from django.contrib.auth.models import User
from django.test import TestCase


class AdminViewSmokeTests(TestCase):
    """Every admin view answers without the host restoring is_ajax."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'suite-admin', 'suite-admin@example.com', 'not-a-real-password')

    def setUp(self):
        self.client.force_login(self.user)

    def test_index_dashboard_renders(self):
        response = self.client.get('/xadmin/')
        self.assertEqual(response.status_code, 200,
                         msg='the dashboard must render without host middleware')

    def test_user_changelist_renders(self):
        response = self.client.get('/xadmin/auth/user/')
        self.assertEqual(response.status_code, 200)

    def test_group_changelist_renders(self):
        response = self.client.get('/xadmin/auth/group/')
        self.assertEqual(response.status_code, 200)

    def test_add_form_renders(self):
        response = self.client.get('/xadmin/auth/user/add/')
        self.assertEqual(response.status_code, 200)

    def test_detail_view_renders(self):
        response = self.client.get('/xadmin/auth/user/{0}/detail/'.format(self.user.pk))
        self.assertEqual(response.status_code, 200)

    def test_update_form_renders(self):
        response = self.client.get('/xadmin/auth/user/{0}/update/'.format(self.user.pk))
        self.assertEqual(response.status_code, 200)


class AjaxBranchTests(TestCase):
    """The AJAX branch is what really exercises the swapped predicate.

    ``BaseAjaxPlugin`` uses ``is_ajax`` to decide between serialising to JSON and
    rendering the whole template, so a botched swap slips past the tests above and only
    shows up here.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'ajax-admin', 'ajax-admin@example.com', 'not-a-real-password')

    def setUp(self):
        self.client.force_login(self.user)

    def test_changelist_serves_json_when_requested_as_ajax(self):
        """With the XHR header and _format=json, the response is JSON, not HTML."""
        response = self.client.get('/xadmin/auth/user/?_format=json',
                                   headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/json', response['Content-Type'],
                      msg='the AJAX branch must still be selected by the header')

    def test_changelist_serves_html_without_the_xhr_header(self):
        """Without the header, the same endpoint renders the full page."""
        response = self.client.get('/xadmin/auth/user/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response['Content-Type'])
