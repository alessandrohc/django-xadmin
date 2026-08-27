# coding=utf-8
"""Import/export resources for the fixture app (#7396).

They exist for one reason: ``import_export_args`` is the only trigger of
``ImportMenuPlugin.init_request`` and ``ExportMenuPlugin.init_request``. With no admin
declaring resource classes, ``ExportMenuPlugin.block_top_toolbar`` -- where the
``ExportForm`` call lives -- runs in no test at all, and that is precisely why the bump
to django-import-export 4.4.1 could read green with the export menu raising TypeError.
"""
from import_export import resources

from test_xadmin.fixtureapp.models import Book


class BookResource(resources.ModelResource):

    class Meta:
        model = Book
        fields = ('id', 'title')
