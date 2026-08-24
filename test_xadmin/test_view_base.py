# coding=utf-8
"""BaseAdminView / CommAdminView: view building, admin urls and model permissions.

Ported from tests/xtests/view_base/tests.py (#7369). The legacy tree could not run past
Django 5.0, so these six tests were not running anywhere.

The old version relied on the suite's global urls.py to resolve /view_base/...; here the
site is mounted from this module instead, and ROOT_URLCONF is overridden onto it, so the
two url assertions stay real instead of being weakened into "some string".
"""
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import path

from xadmin.sites import AdminSite
from xadmin.views import BaseAdminView, CommAdminView, ListAdminView

from test_xadmin.fixtureapp.models import Author, Book

site = AdminSite('views_base')


class AuthorAdmin:
    test_model_attr = 'test_model'
    model_icon = 'flag'


class TestBaseView(BaseAdminView):
    pass


class TestCommView(CommAdminView):
    global_models_icon = {Book: 'test'}


class TestAView(BaseAdminView):
    pass


class OptionA:
    option_attr = 'option_test'


site.register_modelview(r'^list$', ListAdminView, name='%s_%s_list')
site.register_view(r"^test/base$", TestBaseView, 'test')
site.register_view(r"^test/comm$", TestCommView, 'test_comm')
site.register_view(r"^test/a$", TestAView, 'test_a')
site.register(Author, AuthorAdmin)
site.register(Book)

# init() resolves the registered option classes into usable bases (sites.py does this
# for the real site at the end of autodiscover). Without it, get_view_class raises a
# metaclass conflict -- the legacy version of this suite predates the step.
site.init()

# Mounted here so get_admin_url()/get_model_url() have something real to reverse.
urlpatterns = [path('view_base/', site.urls)]


@override_settings(ROOT_URLCONF='test_xadmin.test_view_base')
class BaseAdminTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.test_view_class = site.get_view_class(TestBaseView)
        self.test_view = self.test_view_class()
        self.test_view.setup(self._mocked_request('test/'))

    def _mocked_request(self, url, user='admin'):
        request = self.factory.get(url)
        request.user = (user if isinstance(user, User)
                        else User.objects.create(username=user, is_superuser=True))
        request.session = {}
        return request

    def test_get_view(self):
        """get_view merges the option class and the per-call opts into the instance."""
        test_a = self.test_view.get_view(TestAView, OptionA, opts={'test_attr': 'test'})

        self.assertIsInstance(test_a, TestAView)
        self.assertIsInstance(test_a, OptionA)
        self.assertEqual(test_a.option_attr, 'option_test')
        self.assertEqual(test_a.test_attr, 'test')

    def test_model_view(self):
        """get_model_view merges the registered admin class for that model."""
        test_model = self.test_view.get_model_view(ListAdminView, Author)

        self.assertIsInstance(test_model, AuthorAdmin)
        self.assertEqual(test_model.model, Author)
        self.assertEqual(test_model.test_model_attr, 'test_model')

    def test_admin_url(self):
        self.assertEqual(self.test_view.get_admin_url('test'), '/view_base/test/base')

    def test_model_url(self):
        self.assertEqual(self.test_view.get_model_url(Author, 'list'),
                         '/view_base/xadmin_fixture/author/list')

    def test_has_model_perm(self):
        test_user = User.objects.create(username='test_user')
        self.assertFalse(self.test_view.has_model_perm(Author, 'change', test_user))
        # The request user is a superuser.
        self.assertTrue(self.test_view.has_model_perm(Author, 'change'))


@override_settings(ROOT_URLCONF='test_xadmin.test_view_base')
class CommAdminTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        view_class = site.get_view_class(TestCommView)
        self.test_view = view_class()
        request = self.factory.get('test/comm')
        request.user = User.objects.create(username='admin2', is_superuser=True)
        request.session = {}
        self.test_view.setup(request)

    def test_model_icon(self):
        """model_icon comes from the admin class; global_models_icon overrides per model."""
        self.assertEqual(self.test_view.get_model_icon(Author), 'flag')
        self.assertEqual(self.test_view.get_model_icon(Book), 'test')
