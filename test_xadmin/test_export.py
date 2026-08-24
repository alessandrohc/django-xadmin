# coding=utf-8
"""The export plugin: which formats exist, and what they put in the file. See #7369.

There was no test for export at all before this, which is how the fork ended up
shipping two spreadsheet-injection vectors without anyone noticing.

A cell that starts with ``=``, ``+``, ``-`` or ``@`` is interpreted as a FORMULA by
Excel and Sheets. The victim is whoever opens the file -- typically the manager
downloading a report -- and the attacker only needs to get text into a changelist.

CSV cannot be made safe: it carries no cell type, so the spreadsheet re-guesses every
value on open. Any mitigation means disfiguring the data (the usual advice, prefixing
an apostrophe, would have hit the ``-`` empty-cell placeholder on thousands of cells
here). So CSV was dropped from the export formats instead -- measured decision, see the
ticket. Binary formats carry the type and are safe by construction:

    xlwt  (.xls)  -> ctype=TEXT           already safe, measured
    xlsxwriter    -> <f>1+1</f>           promoted to a real formula
    xlsxwriter with strings_to_formulas=False
                  -> <c t="s"> + sharedStrings <si><t>=1+1</t></si>   text, safe

``xml`` and ``json`` are unaffected: a spreadsheet does not evaluate formulas in them.
"""
import importlib.util
import io
import re
import unittest
import zipfile

from django.contrib.auth.models import User
from django.test import TestCase

from test_xadmin.fixtureapp.models import Author

LIST_URL = '/xadmin/xadmin_fixture/author/'
PAYLOAD = '=1+1'


def missing(*modules):
    """The spreadsheet writers are extras_require, not declared dependencies.

    Skipping keeps the suite honest on an install without the Excel extra, instead of
    reporting a failure that is really an absent optional package. matrix.sh installs
    them, so the cells that matter do exercise these.
    """
    absent = [m for m in modules if importlib.util.find_spec(m) is None]
    return unittest.skipIf(absent, 'requires: {0}'.format(', '.join(absent)))


class ExportFormatTests(TestCase):
    """Which formats the plugin offers at all."""

    def menu_class(self):
        # list_export lives on the menu plugin; the mimes and the writers on the
        # export plugin itself.
        from xadmin.plugins.export import ExportMenuPlugin
        return ExportMenuPlugin

    def plugin_class(self):
        from xadmin.plugins.export import ExportPlugin
        return ExportPlugin

    def test_csv_is_not_an_offered_format(self):
        """Dropped on purpose -- it cannot carry a cell type. See the module docstring."""
        self.assertNotIn('csv', self.menu_class().list_export)

    def test_csv_has_no_mime_entry(self):
        self.assertNotIn('csv', self.plugin_class().export_mimes)

    def test_the_binary_and_data_formats_remain(self):
        offered = set(self.menu_class().list_export)
        self.assertEqual(offered, {'xlsx', 'xls', 'xml', 'json'},
                         msg='dropping csv must not drop anything else')

    def test_every_offered_format_has_a_writer_and_a_mime(self):
        """A format in the list with no get_<fmt>_export is a 500 waiting to happen."""
        plugin = self.plugin_class()
        for fmt in self.menu_class().list_export:
            with self.subTest(fmt=fmt):
                self.assertTrue(hasattr(plugin, 'get_%s_export' % fmt),
                                msg='no writer for the offered format')
                self.assertIn(fmt, plugin.export_mimes)

    def test_the_default_format_is_one_that_still_exists(self):
        """_get_file_spec falls back to a default when export_type is absent.

        It used to default to 'csv'. With the csv writer gone that default would be an
        AttributeError -- a 500 instead of an export -- so this pins that the fallback
        names a format the plugin can actually write.
        """
        import inspect

        from xadmin.plugins.export import ExportPlugin

        source = inspect.getsource(ExportPlugin._get_file_spec)
        match = re.search(r"data\.get\(\s*'export_type'\s*,\s*'([a-z]+)'\s*\)", source)
        self.assertIsNotNone(match, msg='could not read the default export_type')
        default = match.group(1)
        from xadmin.plugins.export import ExportMenuPlugin
        self.assertIn(default, ExportMenuPlugin.list_export,
                      msg='the default export format is not offered any more')
        self.assertTrue(hasattr(ExportPlugin, 'get_%s_export' % default))


class SpreadsheetFormulaTests(TestCase):
    """The file must not carry a formula, whatever the data says."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'export-admin', 'export-admin@example.com', 'not-a-real-password')
        Author.objects.create(name=PAYLOAD, secret='irrelevant')

    def setUp(self):
        self.client.force_login(self.user)

    def _export(self, fmt):
        response = self.client.get(
            '{0}?_do_=export&export_type={1}&all=on'.format(LIST_URL, fmt))
        self.assertEqual(response.status_code, 200,
                         msg='export of {0} must succeed'.format(fmt))
        return response.content

    @missing('xlsxwriter')
    def test_xlsx_stores_the_payload_as_text_not_as_a_formula(self):
        """The strongest of the three vectors: a real <f> element in the file.

        Excel evaluates it on open, with no CSV import dialog in between.
        """
        book = zipfile.ZipFile(io.BytesIO(self._export('xlsx')))
        sheet = book.read('xl/worksheets/sheet1.xml').decode('utf-8')

        self.assertNotIn('<f>', sheet,
                         msg='the workbook carries a formula element; build it with '
                             "{'strings_to_formulas': False}")
        shared = book.read('xl/sharedStrings.xml').decode('utf-8')
        self.assertIn(PAYLOAD, shared,
                      msg='the value must survive as text in the shared string table')

    @missing('xlwt', 'xlrd')
    def test_xls_stores_the_payload_as_text(self):
        """xlwt was already safe; this keeps it that way rather than assuming."""
        import xlrd

        book = xlrd.open_workbook(file_contents=self._export('xls'))
        found = [book.sheet_by_index(0).cell(r, c)
                 for r in range(book.sheet_by_index(0).nrows)
                 for c in range(book.sheet_by_index(0).ncols)]
        payload_cells = [cell for cell in found if cell.value == PAYLOAD]
        self.assertTrue(payload_cells, msg='the payload should appear in the sheet')
        for cell in payload_cells:
            # xlrd ctype 1 is TEXT.
            self.assertEqual(cell.ctype, 1, msg='the cell must be text, not a formula')

    def test_asking_for_csv_does_not_produce_a_file(self):
        """The format is gone; the endpoint must not serve it by URL either.

        list_export gates the MENU, not the endpoint -- measured on the host project,
        where an admin with list_export=() still answered ?_do_=export. So the writer
        being absent is what actually closes it.
        """
        response = self.client.get(
            '{0}?_do_=export&export_type=csv&all=on'.format(LIST_URL))
        self.assertNotEqual(
            response.status_code, 200,
            msg='csv must not be servable, even by a hand-built URL')
