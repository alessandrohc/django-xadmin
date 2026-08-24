# coding=utf-8
"""AdminSite: registration, option merging, plugins and URL building.

Ported from tests/xtests/admin_site/tests.py (#7369), with DummyModel standing in for
the old ModelA. The legacy tree could not run past Django 5.0, so these five tests were
not running anywhere -- and they cover exactly the machinery #7368 changed
(get_view_class and the merged view classes behind site.urls).

Each test builds its own AdminSite, so nothing here touches the global registry.
"""
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from xadmin.sites import AdminSite
from xadmin.views import BaseAdminPlugin, BaseAdminView, ModelAdminView, filter_hook

from test_xadmin.fixtureapp.models import DummyModel


class DummyAdmin:
    pass


class TestAdminView(BaseAdminView):
    site_title = 'TEST TITLE'

    @filter_hook
    def get_title(self):
        return self.site_title

    def get(self, request):
        return HttpResponse(self.site_title)


class TestOption:
    site_title = 'TEST PROJECT'


class TestPlugin(BaseAdminPlugin):

    def get_title(self, title):
        return '%s PLUGIN' % title


class TestModelAdminView(ModelAdminView):

    def get(self, request, obj_id):
        return HttpResponse(str(obj_id))


class AdminSiteTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

    def get_site(self):
        return AdminSite('test', 'test_app')

    def _mocked_request(self, url, user='admin'):
        request = self.factory.get(url)
        request.user = (user if isinstance(user, User)
                        else User.objects.create(username=user, is_superuser=True))
        request.session = {}
        return request

    def test_register_model(self):
        site = self.get_site()
        site.register(DummyModel, DummyAdmin)
        self.assertIn(DummyModel, site._registry.keys())

    def test_unregister_model(self):
        site = self.get_site()
        site.register(DummyModel, DummyAdmin)
        site.unregister(DummyModel)
        self.assertNotIn(DummyModel, site._registry.keys())

    def test_viewoption(self):
        """An option class registered on a view is merged into the built class."""
        site = self.get_site()
        site.register_view(r"^test/$", TestAdminView, 'test')
        site.register(TestAdminView, TestOption)
        # init() is what turns the registered AdminViewOption into a usable class
        # (sites.py resolves _registry_avs there). The legacy version of this test
        # predates that step and blew up with a metaclass conflict without it -- the
        # test was incomplete, not the code.
        site.init()

        built = site.get_view_class(TestAdminView)
        self.assertEqual(built.site_title, 'TEST PROJECT')

    def test_plugin(self):
        """The plugin's filter_hook wraps the view's own method."""
        site = self.get_site()
        site.register_view(r"^test/$", TestAdminView, 'test')
        site.register_plugin(TestPlugin, TestAdminView)

        built = site.get_view_class(TestAdminView)
        self.assertIn(TestPlugin, built.plugin_classes)

        view = built()
        view.setup(self._mocked_request('test/'))
        self.assertEqual(view.get_title(), 'TEST TITLE PLUGIN')

    def test_get_urls(self):
        """site.urls still returns (patterns, app_name, namespace).

        #7368 made the per-model view classes build lazily; this pins that the URL
        tuple itself did not change shape.
        """
        site = self.get_site()
        site.register(DummyModel, DummyAdmin)
        site.register_view(r"^test/$", TestAdminView, 'test')
        site.register_modelview(r'^(.+)/test/$', TestModelAdminView, name='%s_%s_test')

        urls, app_name, namespace = site.urls

        self.assertEqual(app_name, 'test')
        self.assertEqual(namespace, 'test_app')
        self.assertTrue(urls, msg='the site must produce url patterns')
