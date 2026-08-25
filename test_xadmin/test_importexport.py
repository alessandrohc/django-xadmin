# coding=utf-8
"""The import/export plugin actually runs. See #7369.

There was no coverage of this plugin at all, and the matrix reported PASSED with a
deterministic 500 inside it: django-import-export 3.0 moved the read mode from a call
argument into BaseStorage.__init__, so

    tmp_storage.save(data, input_format.get_read_mode())
    tmp_storage.read(input_format.get_read_mode())

pass one positional too many. Measured against the installed 3.3.9:

    BaseStorage.__init__ (self, **kwargs)
    save()               (self, data)
    read()               (self)

The views are registered for EVERY model (site.register_modelview at the bottom of the
plugin), so any staff user with add+change reaches /import/, sees the form render, and
gets a 500 on upload.
"""
import io

from django.contrib.auth.models import User
from django.test import TestCase

from test_xadmin.fixtureapp.models import Author

IMPORT_URL = '/xadmin/xadmin_fixture/author/import/'
LIST_URL = '/xadmin/xadmin_fixture/author/'


class ImportViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'ie-admin', 'ie-admin@example.com', 'not-a-real-password')
        Author.objects.create(name='existing', secret='s')

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_import_form_renders(self):
        """The easy half -- it already worked, and it is what lures the user in."""
        response = self.client.get(IMPORT_URL)
        self.assertEqual(response.status_code, 200)

    def test_uploading_a_file_does_not_500(self):
        """The defect: the upload path reached TmpStorage with the old signature.

        A CSV payload is used deliberately even though CSV was dropped from EXPORT:
        import formats come from django-import-export, which is a separate list, and
        the point here is the storage round-trip, not the format.
        """
        payload = 'id,name,secret,is_active,is_featured\n,imported,s,True,\n'
        upload = io.BytesIO(payload.encode('utf-8'))
        upload.name = 'authors.csv'

        response = self.client.post(
            IMPORT_URL, {'import_file': upload, 'input_format': 0}, follow=False)

        self.assertNotEqual(
            response.status_code, 500,
            msg='the upload must not raise; TmpStorage takes the read mode in '
                '__init__ since django-import-export 3.0, not as a call argument')
        self.assertIn(response.status_code, (200, 302),
                      msg='expected the confirm page or a redirect')


class ExportActionTests(TestCase):
    """The other entry point: ?_action_=export, which goes through tablib."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'ie-admin2', 'ie-admin2@example.com', 'not-a-real-password')
        Author.objects.create(name='exported', secret='s')

    def setUp(self):
        self.client.force_login(self.user)

    def test_export_action_returns_a_file(self):
        response = self.client.get(
            '{0}?_action_=export&file_format=0&scope=all'.format(LIST_URL))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content, msg='the export must not be empty')
