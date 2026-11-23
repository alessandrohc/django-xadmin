# coding=utf-8
"""Registered through xadmin's normal autodiscover, exactly as a real app would be.

``reversion_enable = True`` is the flag ``xversion.register_models()`` looks for, and
``inlines`` is a plain list rather than a property on purpose -- register_models()
deliberately skips admins whose ``inlines`` is a property, so a property here would make
the positive test pass for the wrong reason.
"""
from django.http import HttpResponse

from xadmin.plugins.actions import BaseActionView
from xadmin.sites import site
from xadmin.views import BaseAdminPlugin  # noqa: F401  (keeps import order honest)

from test_xadmin.fixtureapp.models import Author, Book, Note, Ranked, RankedUuid, Tag
from test_xadmin.fixtureapp.resources import BookResource


class BookInline:
    model = Book
    extra = 1
    style = 'tab'


class AuthorAdmin:
    list_display = ('name',)
    reversion_enable = True
    inlines = [BookInline]


class BookAdmin:
    list_display = ('title',)
    # #7396: both import/export menu plugins only wake up when they find a resource
    # class here. Book carries that role so the changelist exercises
    # ExportMenuPlugin.block_top_toolbar (where the ExportForm call lives) and the
    # import button, neither of which had any coverage. Author deliberately stays
    # without import_export_args: it keeps the modelresource_factory() fallback covered.
    import_export_args = {
        'import_resource_class': BookResource,
        'export_resource_class': BookResource,
    }


class MarkSeenAction(BaseActionView):
    """Probe action (#8930): renames the selected rows and answers nothing, so the list redirects."""
    action_name = 'mark_seen'
    description = 'Mark the selected rows as seen'
    model_perm = 'change'

    def do_action(self, queryset):
        queryset.update(name='seen')


class ReplyAction(BaseActionView):
    """Probe action (#8930): answers with a response of its own, which the POST must return as it is."""
    action_name = 'reply'
    description = 'Answer with the number of selected rows'
    model_perm = 'change'

    def do_action(self, queryset):
        return HttpResponse('replied %d' % queryset.count())


class RankedAdmin:
    list_display = ('name', 'position')
    # turns the sortable list on: the ``save-order`` route reads it (#8930)
    list_order_field = 'position'
    # ``?_q_=<text>`` narrows the list: a selected id the list leaves out must not be acted on (#8930)
    search_fields = ('name',)
    actions = [MarkSeenAction, ReplyAction]

    @property
    def formats(self):
        # só os binários, como os admins do plus_base que restringem a lista (User, Section e Info do plus_base e o do
        # plus_log_http): o índice do `format` da exportação vale contra esta lista, não contra a padrão (#8985). O xadmin
        # copia o atributo que não é chamável para o plugin (`_get_merge_attrs`, sites.py:436-442), então o `self` aqui é o
        # plugin e a property não lê nada dele
        from import_export.formats import base_formats
        return (base_formats.XLS, base_formats.XLSX)


class TagAdmin:
    list_display = ('name',)
    actions = [MarkSeenAction]


class RankedUuidAdmin:
    list_display = ('name', 'position')
    # the sortable list on a UUID key: the ``save-order`` route converts the ids with the key field (#8986)
    list_order_field = 'position'


class NoteAdmin:
    list_display = ('name',)
    # each user only sees the notes they own: the ``ModelPermissionPlugin`` narrows ``queryset()`` (plugins/auth.py:154-163).
    # The owner is left out of ``list_display`` on purpose: the plugin removes it for a non-superuser (auth.py:165-170)
    user_can_access_owned_objects_only = True
    user_owned_objects_field = 'owner'


site.register(Author, AuthorAdmin)
site.register(Book, BookAdmin)
site.register(Ranked, RankedAdmin)
site.register(Tag, TagAdmin)
site.register(RankedUuid, RankedUuidAdmin)
site.register(Note, NoteAdmin)
