# coding=utf-8
"""The import/export plugin actually runs (#7369, #7396).

#7369 -- this plugin had no coverage at all, and the matrix reported PASSED with a
deterministic 500 inside it: django-import-export 3.0 took the read mode out of the
call arguments and moved it into BaseStorage.__init__, so

    tmp_storage.save(data, input_format.get_read_mode())
    tmp_storage.read(input_format.get_read_mode())

were passing one positional too many.

#7396 -- the bump to 4.4.1 (which closed the tablib CVE) reworked the library's FORM
SURFACE, and the fork's plugin calls all of it by hand:

    ImportForm.__init__       3.3.9  (self, import_formats, *args, **kwargs)
                              4.4.1  (self, formats, resources, **kwargs)
                                     `resources` positional and MANDATORY, no *args
    format field              3.3.9  ImportForm.input_format / ExportForm.file_format
                              4.4.1  ImportExportFormBase.format, in both
    ConfirmImportForm         3.3.9  input_format field
                              4.4.1  format field
    ExportForm.__init__       3.3.9  (self, formats, *args, **kwargs)
                              4.4.1  inherits ImportExportFormBase (formats, resources)

The field name matters twice over: the export modal issues its GET using the field name,
and the confirmation form travels between two requests in a hidden field. A test that
POSTs the old key turns the form INVALID -- the dry-run branch never runs, the view
returns 200 and the test passes proving nothing. That is why the upload tests here read
the format index off the rendered form itself and require the preview to appear.

The import views are registered for EVERY model (site.register_modelview at the end of
the plugin), so any staff user with add+change reaches /import/.
"""
import io
import re

from django.contrib.auth.models import User
from django.test import TestCase

from test_xadmin.fixtureapp.models import Author, Book

IMPORT_URL = '/xadmin/xadmin_fixture/author/import/'
PROCESS_URL = '/xadmin/xadmin_fixture/author/process_import/'
LIST_URL = '/xadmin/xadmin_fixture/author/'
# Book is the model that declares import_export_args, hence the only one whose
# changelist builds the import/export menus. See test_xadmin/fixtureapp/adminx.py.
BOOK_LIST_URL = '/xadmin/xadmin_fixture/book/'


class ImportViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'ie-admin', 'ie-admin@example.com', 'not-a-real-password')
        Author.objects.create(name='existing', secret='s')

    def setUp(self):
        self.client.force_login(self.user)

    def format_value(self, response, title):
        """The index the form ITSELF offers for the requested format.

        Read off the rendered form instead of recomputed: the index is a position within
        get_import_formats(), and redoing that arithmetic in the test would duplicate the
        logic under test. Reading from the form also makes this helper bite the field
        rename.
        """
        field = response.context['form'].fields['format']
        for value, label in field.choices:
            if label == title:
                return value
        self.fail('the import form offers no {0!r} format; choices were {1!r}'
                  .format(title, list(field.choices)))

    def upload(self):
        """POST a valid CSV to /import/ and return the preview response."""
        page = self.client.get(IMPORT_URL)
        self.assertEqual(page.status_code, 200)

        payload = 'id,name,secret,is_active,is_featured\n,imported,s,True,\n'
        upload = io.BytesIO(payload.encode('utf-8'))
        upload.name = 'authors.csv'

        return self.client.post(IMPORT_URL, {
            'import_file': upload,
            'format': self.format_value(page, 'csv'),
        }, follow=False)

    def test_the_import_form_renders(self):
        """The easy half -- it already worked, and it is what draws the user in.

        It is also the first site to die on the bump: 4.4.1 refuses
        ImportForm(formats, data, files) with TypeError before any template.
        """
        response = self.client.get(IMPORT_URL)
        self.assertEqual(response.status_code, 200)

    def test_the_import_form_hides_the_resource_field_without_a_label(self):
        """4.x made `resource` mandatory on the form; with one resource it is hidden.

        The template iterated the whole form emitting a label for every field, so the
        new field showed up as an empty label row on the import page.
        """
        content = self.client.get(IMPORT_URL).content.decode('utf-8')

        self.assertIn('name="resource"', content,
                      msg='the 4.x import form must carry the resource field; without '
                          'it the form was built with the 3.x signature')
        self.assertNotIn('for="id_resource"', content,
                         msg='a hidden field must not render a label row; iterate '
                             'field.is_hidden in the import template')

    def test_uploading_a_file_does_not_500(self):
        """The #7369 defect: the upload reached TmpStorage with the old signature.

        The CSV is deliberate even with CSV dropped from EXPORT: the import formats come
        from django-import-export, which is a different list, and what is measured here
        is the round trip through the storage.
        """
        response = self.upload()

        self.assertNotEqual(
            response.status_code, 500,
            msg='the upload must not raise; TmpStorage takes the read mode in '
                '__init__ since django-import-export 3.0, not as a call argument')
        self.assertIn(response.status_code, (200, 302),
                      msg='expected the confirm page or a redirect')

    def test_uploading_a_file_reaches_the_preview(self):
        """The assertion that blocks the #7396 false green.

        If the format field name is wrong the form stays INVALID, the dry-run branch
        never runs and the view still returns 200. Requiring the preview and the
        confirm_form is what proves the upload was actually processed.
        """
        response = self.upload()

        self.assertEqual(response.status_code, 200)
        self.assertIn('result', response.context,
                      msg='the dry-run never ran: the import form did not validate, '
                          'which is what a wrong format field name looks like')
        self.assertFalse(response.context['result'].has_errors(),
                         msg='the dry-run reported row errors')
        self.assertIn('confirm_form', response.context,
                      msg='a clean dry-run must produce the confirm form')

    def test_confirming_the_upload_imports_the_row(self):
        """The second request: /process_import/, which reads the format off the hidden field.

        This is the only path that exercises ImportProcessView, and the hidden field that
        travels between the two requests was renamed from `input_format` to `format` in
        4.x. Without this test that site had no coverage at all.
        """
        confirm_form = self.upload().context['confirm_form']

        payload = dict(confirm_form.initial)
        payload['confirm'] = 'Confirm import'
        response = self.client.post(PROCESS_URL, payload)

        self.assertEqual(response.status_code, 302,
                         msg='a valid confirm form must redirect to the changelist; '
                             'an invalid one falls off the end of post() and Django '
                             'raises "did not return an HttpResponse"')
        self.assertTrue(
            Author.objects.filter(name='imported').exists(),
            msg='the confirmed import must actually write the row')


class ExportMenuTests(TestCase):
    """The export menu on the changelist -- where the ExportForm call lives.

    It only shows up on an admin with import_export_args, and no fixture model had that
    before #7396: the ExportForm(formats) call broke on 4.4.1 without the suite noticing.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'ie-menu', 'ie-menu@example.com', 'not-a-real-password')
        author = Author.objects.create(name='menu-author', secret='s')
        Book.objects.create(author=author, title='menu-book')

    def setUp(self):
        self.client.force_login(self.user)

    def changelist(self):
        response = self.client.get(BOOK_LIST_URL)
        self.assertEqual(response.status_code, 200,
                         msg='the changelist of a model with import_export_args must '
                             'render; building ExportForm with the 3.x signature '
                             'raises TypeError right here')
        return response.content.decode('utf-8')

    def test_the_changelist_renders_the_export_menu(self):
        content = self.changelist()

        self.assertIn('id="export-modal"', content,
                      msg='the import/export modal is missing from the toolbar')
        self.assertIn('id="export-menu"', content,
                      msg='the button that opens the modal is missing')

    def test_the_changelist_renders_the_import_button(self):
        self.assertIn(BOOK_LIST_URL + 'import/', self.changelist(),
                      msg='ImportMenuPlugin must add the import link for an admin '
                          'that declares import_resource_class')

    def test_the_export_menu_styles_the_format_select(self):
        """_form_bootstrap_styles iterates a list of field names.

        The list said `file_format`, the 3.x name. In 4.x the field is called `format`,
        and the `for` matches nothing: the select loses form-control and required
        SILENTLY -- no exception, just an unstyled modal with no client-side validation.
        """
        content = self.changelist()

        select = re.search(r'<select[^>]*\bname="format"[^>]*>', content)
        self.assertIsNotNone(
            select, msg='no format select in the export modal; the 4.x field is named '
                        'format, not file_format')
        self.assertIn('form-control', select.group(0),
                      msg='_form_bootstrap_styles must name the field the running '
                          'version actually has')

    def test_the_export_menu_hides_the_resource_field_without_a_label(self):
        """`resource` and `export_items` arrive hidden and must not build a form-group."""
        content = self.changelist()

        self.assertIn('name="resource"', content,
                      msg='the 4.x export form must carry the resource field')
        self.assertNotIn('for="id_resource"', content,
                         msg='a hidden field must not render a label; iterate '
                             'field.is_hidden in the export toolbar template')


class ExportActionTests(TestCase):
    """The other way in: ?_action_=export, which goes through tablib."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'ie-admin2', 'ie-admin2@example.com', 'not-a-real-password')
        Author.objects.create(name='exported', secret='s')

    def setUp(self):
        self.client.force_login(self.user)

    def assert_served_a_file(self, query):
        response = self.client.get('{0}?{1}'.format(LIST_URL, query))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content, msg='the export must not be empty')
        return response

    def test_export_action_returns_a_file(self):
        """The 3.x parameter name, kept on purpose.

        The modal stopped emitting `file_format` in 4.x, but the view still accepts the
        old name so a hand-built or bookmarked export URL keeps working. This test is
        what stops that compatibility from being removed by accident.
        """
        self.assert_served_a_file('_action_=export&file_format=0&scope=all')

    def test_export_action_accepts_the_4x_format_param(self):
        """The name the modal actually sends after #7396.

        Without it the view reads `file_format`, finds nothing, and every export turned
        into the "You must select an export format." warning plus a redirect -- a 302,
        never a file.
        """
        self.assert_served_a_file('_action_=export&format=0&scope=all')
