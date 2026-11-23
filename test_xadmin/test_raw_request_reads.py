# coding=utf-8
"""The quick edit, the bookmark and the export selection answer 400, not 500, to input the screen never sends. See #8985.

Three plugins read ``request.GET``/``request.POST`` without a form: ``request.GET['fields']`` of the quick edit
(``plugins/editable.py``), ``request.POST['title']`` and the other raw values of the bookmark (``plugins/bookmark.py``) and,
in the export, ``_selected_actions`` in a ``pk__in`` and ``int(format)`` (``plugins/importexport.py``). A missing parameter,
an id the key field does not convert or a ``format`` that is not an index of the formats gave a 500, and on SQLite a number
the column cannot hold gave one too. The tests pin the answer now: 400 before anything is saved, exported or sent, an id no
object owns exported as nothing, and everything the screen does send unchanged.

The module also pins what must not move around those lines: the 403 and the 404 that come before the new checks, the
warnings (302) that win over the 400, the scopes that ignore the selection, ``file_format``, the e-mail delivery, the
visibility of the export (an object the admin does not show to the user is never exported) and the gate of the site.

The bookmark plugin is excluded by the host project (``XADMIN_EXCLUDE_PLUGINS``), so its tests hold for the package alone.

The client is built with ``raise_request_exception=False`` so a 500 shows up as a status to compare, and the red fails on
the assertion instead of erroring on the exception. The attribute ``client`` of the ``TestCase`` is left alone: the actor's
client is built per call.

    python runtests.py test_xadmin.test_raw_request_reads
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_raw_request_reads'
"""
import csv
import io
import json
import uuid
from unittest import mock

from django.contrib.auth.models import Permission, User
from django.contrib.messages import WARNING, get_messages
from django.test import Client, TestCase
import tablib
from import_export.formats.base_formats import DEFAULT_FORMATS

from xadmin.models import Bookmark
from xadmin.plugins.importexport import ExportPlugin
from xadmin.sites import site
from xadmin.views.list import ListAdminView

from test_xadmin.fixtureapp.models import Author, Note, Ranked, Tag

AUTHOR_LIST = '/xadmin/xadmin_fixture/author/'
TAG_LIST = '/xadmin/xadmin_fixture/tag/'
NOTE_LIST = '/xadmin/xadmin_fixture/note/'
RANKED_LIST = '/xadmin/xadmin_fixture/ranked/'
BOOKMARK_URL = AUTHOR_LIST + 'bookmark/'

# uma chave inteira que nenhum objeto tem (a mesma de test_invalid_post_input.py e test_save_order_route.py), o número que
# nenhuma coluna inteira comporta (30 dígitos: o SQLite estoura no `pk__in`) e o texto que nenhuma chave converte
MISSING_ID = '987654321'
HUGE_ID = '1' + '0' * 29
NUL_ID = '1\x00'

CSV_TYPE = 'text/csv'
XLS_TYPE = 'application/vnd.ms-excel'
XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _staff(username, *codenames):
    """Staff user (xadmin only lets staff in) with exactly the given permissions."""
    user = User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password',
                                    is_staff=True)
    if codenames:
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def _table(model):
    """The whole table of the model, to compare before and after a request."""
    return list(model.objects.order_by('pk').values())


def _patch_url(list_url, pk):
    return f'{list_url}{pk}/patch/'


class _ClientMixin:
    """A client that turns an unhandled exception into a 500 response instead of raising."""

    def _client_for(self, user=None, **extra):
        client = Client(raise_request_exception=False, **extra)
        if user is not None:
            client.force_login(user)
        return client


class _ExportMixin(_ClientMixin):
    """The request of the export, the CSV read back and the checks every export test repeats."""

    def _export(self, user, list_url, **params):
        """GET the export of the list; ``params`` is the rest of the query string (``result-action`` goes through ``**{}``)."""
        query = dict({'_action_': 'export'}, **params)
        return self._client_for(user).get(list_url, query)

    def _assert_csv_ids(self, response, expected, msg):
        """200 with a CSV file whose rows are exactly the ``expected`` keys (the header must be there even with no rows)."""
        self.assertEqual(response.status_code, 200, msg=msg)
        self.assertTrue(response['Content-Type'].startswith(CSV_TYPE), msg=f'{msg}: the file must be a CSV')
        reader = csv.DictReader(io.StringIO(response.content.decode('utf-8')))
        rows = list(reader)
        self.assertIn('id', reader.fieldnames or [], msg=f'{msg}: the file must carry the header row')
        self.assertEqual(sorted(str(row['id']) for row in rows), sorted(str(pk) for pk in expected), msg=msg)

    def _assert_served(self, response, content_type, msg):
        """200 with a file of the given type, as an attachment."""
        self.assertEqual(response.status_code, 200, msg=msg)
        self.assertTrue(response['Content-Type'].startswith(content_type),
                        msg=f'{msg}: expected {content_type}, got {response["Content-Type"]}')
        self.assertIn('attachment', response['Content-Disposition'], msg=f'{msg}: the file must be an attachment')

    def _assert_refused(self, response, msg):
        """400 and no file at all."""
        self.assertEqual(response.status_code, 400, msg=msg)
        self.assertFalse(response.has_header('Content-Disposition'), msg=f'{msg}: no file may be served')

    def _assert_warned(self, response, msg):
        """302 back to the list with one warning, and no file."""
        self.assertEqual(response.status_code, 302, msg=msg)
        self.assertEqual(response['Location'], AUTHOR_LIST, msg=f'{msg}: the warning sends the user back to the list')
        self.assertFalse(response.has_header('Content-Disposition'), msg=f'{msg}: no file may be served')
        levels = [message.level for message in get_messages(response.wsgi_request)]
        self.assertEqual(levels, [WARNING], msg=f'{msg}: the redirect must carry one warning')


class EditPatchTests(_ClientMixin, TestCase):
    """The quick edit (``<id>/patch/``, GET) without the ``fields`` the screen always sends."""

    @classmethod
    def setUpTestData(cls):
        cls.changer = _staff('raw-patch-changer', 'view_author', 'change_author', 'view_tag', 'change_tag')
        cls.viewer = _staff('raw-patch-viewer', 'view_author', 'view_tag')
        cls.blind = _staff('raw-patch-blind')
        cls.author = Author.objects.create(name='author-one')
        cls.tag = Tag.objects.create(name='tag-one')

    def _targets(self):
        """(label, model, list url, an object's key): the integer key and the UUID key."""
        return (('author', Author, AUTHOR_LIST, self.author.pk), ('tag', Tag, TAG_LIST, self.tag.pk))

    def test_quick_edit_without_fields_answers_400(self):
        """AC-8985-1: a edição rápida de quem pode alterar o objeto, sem o parâmetro `fields`, responde 400."""
        for name, model, list_url, pk in self._targets():
            before = _table(model)
            client = self._client_for(self.changer)
            # controle positivo: o mesmo ator e o mesmo objeto com o `fields` que a tela manda abrem o formulário, então o
            # 400 abaixo é do parâmetro que falta e não de permissão ou de ambiente
            control = client.get(_patch_url(list_url, pk), {'fields': 'name'})
            self.assertEqual(control.status_code, 200, msg=f'{name}: control: the screen\'s own request must open the form')
            with self.subTest(model=name):
                response = client.get(_patch_url(list_url, pk))
                self.assertEqual(response.status_code, 400, msg='a quick edit without fields must answer 400')
                self.assertEqual(_table(model), before, msg='nothing may be saved')

    def test_quick_edit_with_fields_keeps_answering_the_form_of_the_editable_fields(self):
        """AC-8985-2: com `fields` vazio, de campos editáveis, da chave inteira ou de nomes que o modelo não tem, segue em 200."""
        cases = {
            # (consulta, o campo `name` deve estar no formulário, modelos em que o caso vale): a chave inteira numerada pelo
            # banco (o `Author` usa `BigAutoField`) é editável, mas o formulário não a oferece (`AutoFieldMixin.formfield`
            # devolve nulo: django/db/models/fields/__init__.py:2833), então nunca aparece. O caso vale só no `Author`: a chave
            # UUID do `Tag` é `editable=False` e nomeá-la em `fields` dá 500 (`FieldError`) hoje, fora deste ticket (medido em
            # `runs/diag-7.log`; follow-up F5 e adendo em `notes/entrega.md`)
            'field name': ('?fields=name', True, ('author', 'tag')),
            'empty': ('?fields=', False, ('author', 'tag')),
            'name the model lacks': ('?fields=zzz', False, ('author', 'tag')),
            'automatic key field': ('?fields=id', False, ('author',)),
            'field name and automatic key field': ('?fields=name,id', True, ('author',)),
        }
        for name, model, list_url, pk in self._targets():
            before = _table(model)
            for case, (query, has_name, models) in cases.items():
                if name not in models:
                    continue
                with self.subTest(model=name, case=case):
                    response = self._client_for(self.changer).get(_patch_url(list_url, pk) + query)
                    self.assertEqual(response.status_code, 200, msg='a quick edit with fields must answer 200')
                    html = response.content.decode('utf-8')
                    self.assertEqual('name="name"' in html, has_name,
                                     msg='the form must hold the editable fields asked for, and only those')
                    self.assertNotIn('name="id"', html, msg='the key field is not editable: it never shows up')
            self.assertEqual(_table(model), before, msg='a GET must not save anything')

    def test_quick_edit_without_change_permission_answers_403_before_the_missing_fields(self):
        """AC-8985-3 (controle): sem permissão de alterar a edição rápida sem `fields` responde 403, nunca 400."""
        for name, model, list_url, pk in self._targets():
            # controle positivo: com a permissão o mesmo objeto abre
            control = self._client_for(self.changer).get(_patch_url(list_url, pk), {'fields': 'name'})
            self.assertEqual(control.status_code, 200, msg=f'{name}: control: the actor who can change must open the form')
            for who, user in (('view permission only', self.viewer), ('no permission', self.blind)):
                with self.subTest(model=name, who=who):
                    response = self._client_for(user).get(_patch_url(list_url, pk))
                    self.assertEqual(response.status_code, 403, msg='who cannot change the model gets 403, not 400')

    def test_quick_edit_of_an_object_that_does_not_exist_answers_404_before_the_missing_fields(self):
        """AC-8985-4 (controle): a edição rápida sem `fields` de um objeto que não existe responde 404, nunca 400."""
        for name, model, list_url, pk in self._targets():
            control = self._client_for(self.changer).get(_patch_url(list_url, pk), {'fields': 'name'})
            self.assertEqual(control.status_code, 200, msg=f'{name}: control: an object that exists must open the form')
            missing = MISSING_ID if name == 'author' else str(uuid.uuid4())
            for case, key in (('an id no object owns', missing), ('text that is not a key', 'abc')):
                with self.subTest(model=name, case=case):
                    response = self._client_for(self.changer).get(_patch_url(list_url, key))
                    self.assertEqual(response.status_code, 404, msg='an object that does not exist gets 404, not 400')


class BookmarkRawInputTests(_ClientMixin, TestCase):
    """The creation of a bookmark (``bookmark/``, POST) with what the screen never sends (the package alone)."""

    @classmethod
    def setUpTestData(cls):
        cls.bookmarker = _staff('raw-bookmarker', 'view_author', 'add_bookmark')
        cls.author = Author.objects.create(name='bookmark-author')

    def _post(self, data):
        return self._client_for(self.bookmarker).post(BOOKMARK_URL, data)

    def test_invalid_bookmark_input_answers_400_and_creates_nothing(self):
        """AC-8985-5: o favorito sem `title`, com texto maior que o campo, com o byte NUL ou com `is_share` ruim dá 400."""
        # os limites dos casos abaixo são os do campo: o teste se desfaz se a coluna mudar
        self.assertEqual(Bookmark._meta.get_field('title').max_length, 128)
        self.assertEqual(Bookmark._meta.get_field('query').max_length, 1000)
        # controle positivo: o formulário da lista (`title` e `query`) cria o favorito pela mesma rota e pelo mesmo ator
        before = Bookmark.objects.count()
        control = self._post({'title': 'mine', 'query': 'a=1'})
        self.assertEqual(control.status_code, 200, msg='control: the list form must create a bookmark')
        self.assertEqual(Bookmark.objects.count(), before + 1, msg='control: the bookmark must be created')

        cases = {
            'no title next to a query': {'query': 'a=1'},
            'no data at all': {},
            'title of 129 characters': {'title': 'x' * 129},
            'query of 1001 characters': {'title': 'x', 'query': 'q' * 1001},
            'NUL byte in the title': {'title': 'a\x00b'},
            'NUL byte in the query': {'title': 'x', 'query': 'a=\x00'},
        }
        for bad in ('on', 'abc', 'true', '2', ''):
            cases[f'is_share {bad!r}'] = {'title': 'x', 'is_share': bad}
        for case, data in cases.items():
            with self.subTest(case=case):
                before = Bookmark.objects.count()
                response = self._post(data)
                self.assertEqual(response.status_code, 400, msg='an invalid bookmark must answer 400')
                self.assertEqual(Bookmark.objects.count(), before, msg='no bookmark may be created')

    def test_valid_bookmark_input_keeps_creating_the_bookmark(self):
        """AC-8985-6: `title` (também vazio), `query` (também ausente) e `is_share` que o campo converte criam o favorito."""
        cases = [
            ('title and query', {'title': 'mine', 'query': 'a=1&b=2'}, False),
            ('title only', {'title': 'mine'}, False),
            ('empty title', {'title': ''}, False),
            ('title of exactly 128 characters', {'title': 't' * 128}, False),
            ('query of exactly 1000 characters', {'title': 'x', 'query': 'q' * 1000}, False),
        ]
        # os seis valores que o `BooleanField` do favorito converte; ausente é o padrão (falso)
        for share, shared in (('0', False), ('1', True), ('True', True), ('False', False), ('t', True), ('f', False)):
            cases.append((f'is_share {share!r}', {'title': 'x', 'is_share': share}, shared))
        for case, data, shared in cases:
            with self.subTest(case=case):
                before = Bookmark.objects.count()
                response = self._post(data)
                self.assertEqual(response.status_code, 200, msg='a valid bookmark must be created')
                self.assertEqual(Bookmark.objects.count(), before + 1, msg='exactly one bookmark must be created')
                answer = json.loads(response.content)
                query = data.get('query', '')
                self.assertEqual(answer['title'], data['title'], msg='the answer must carry the title')
                self.assertEqual(answer['url'], AUTHOR_LIST + (f'?{query}' if query else ''),
                                 msg='the answer must carry the address of the list with the query')
                row = Bookmark.objects.order_by('-pk').first()
                self.assertEqual((row.user, row.title, row.query, row.is_share), (self.bookmarker, data['title'], query, shared),
                                 msg='the row must hold what was sent, with the user and the converted is_share')


class ExportSelectionTests(_ExportMixin, TestCase):
    """The selection of the export (``?_action_=export&scope=selected&_selected_actions=…``, GET)."""

    @classmethod
    def setUpTestData(cls):
        cls.viewer = _staff('raw-export-viewer', 'view_author', 'view_tag', 'view_note', 'view_ranked')
        cls.owner_a = _staff('raw-owner-a', 'view_note')
        cls.owner_b = _staff('raw-owner-b', 'view_note')
        cls.a1 = Author.objects.create(name='a1')
        cls.a2 = Author.objects.create(name='a2')
        cls.a3 = Author.objects.create(name='a3')
        cls.t1 = Tag.objects.create(name='t1')
        cls.t2 = Tag.objects.create(name='t2')
        cls.na1 = Note.objects.create(name='a-one', owner=cls.owner_a)
        cls.na2 = Note.objects.create(name='a-two', owner=cls.owner_a)
        cls.nb = Note.objects.create(name='b-one', owner=cls.owner_b)

    def _selected(self, list_url, selected, user=None, **extra):
        """The export of a selection as CSV (format 0), by ``user`` (the viewer by default)."""
        return self._export(user or self.viewer, list_url, format='0', scope='selected', _selected_actions=selected, **extra)

    def _author_ids(self):
        return [self.a1.pk, self.a2.pk, self.a3.pk]

    def test_selection_item_the_key_does_not_convert_answers_400_and_exports_nothing(self):
        """AC-8985-7: o item de `_selected_actions` que a chave não converte dá 400, sem arquivo (chave inteira e UUID)."""
        # controle positivo: a mesma rota e o mesmo ator exportam uma seleção válida
        self._assert_csv_ids(self._selected(AUTHOR_LIST, str(self.a1.pk)), [self.a1.pk], 'control: a valid selection exports')
        cases = [
            ('author', AUTHOR_LIST, 'text', 'abc', {}),
            ('author', AUTHOR_LIST, 'empty item between commas', '1,,2', {}),
            ('author', AUTHOR_LIST, 'an id and then text', f'{self.a1.pk},abc', {}),
            ('author', AUTHOR_LIST, 'NUL byte', NUL_ID, {}),
            # só o valor exato `1` de `_select_across` é «todos os registros» (importexport.py:436)
            ('author', AUTHOR_LIST, 'text with _select_across=0', 'abc', {'_select_across': '0'}),
            ('author', AUTHOR_LIST, 'text with _select_across=x', 'abc', {'_select_across': 'x'}),
            ('tag', TAG_LIST, 'text that is not a UUID', 'abc', {}),
            ('tag', TAG_LIST, 'NUL byte', NUL_ID, {}),
            ('tag', TAG_LIST, 'a UUID and then text', f'{self.t1.pk},abc', {}),
        ]
        for model, list_url, case, selected, extra in cases:
            with self.subTest(model=model, case=case):
                self._assert_refused(self._selected(list_url, selected, **extra),
                                     'an item the key does not convert must answer 400')

    def test_invalid_selection_is_refused_before_the_e_mail_delivery(self):
        """AC-8985-7: a seleção com item que a chave não converte dá 400 também no envio por e-mail, sem enviar."""
        with mock.patch.object(ExportPlugin, 'send_mail') as sendmail:
            # controle positivo: a seleção válida chega ao envio uma vez, então o dublê está ligado
            self._selected(AUTHOR_LIST, str(self.a1.pk), **{'result-action': 'sendmail'})
            self.assertEqual(sendmail.call_count, 1, msg='control: a valid selection must reach the delivery')
            sendmail.reset_mock()
            for model, list_url, case, selected in (('author', AUTHOR_LIST, 'text', 'abc'),
                                                    ('author', AUTHOR_LIST, 'empty item between commas', '1,,2'),
                                                    ('author', AUTHOR_LIST, 'NUL byte', NUL_ID),
                                                    ('tag', TAG_LIST, 'text that is not a UUID', 'abc')):
                with self.subTest(model=model, case=case):
                    self._assert_refused(self._selected(list_url, selected, **{'result-action': 'sendmail'}),
                                         'an invalid selection must answer 400 instead of sending')
                    self.assertEqual(sendmail.call_count, 0, msg='nothing may be sent')

    def test_selection_number_the_column_cannot_hold_is_an_id_no_object_owns(self):
        """AC-8985-8: o número fora do intervalo do banco é um id sem objeto: exporta só os que existem, sem erro."""
        # controle positivo: o id de um objeto, pela mesma rota, exporta o objeto
        self._assert_csv_ids(self._selected(AUTHOR_LIST, str(self.a1.pk)), [self.a1.pk], 'control: an id of an object exports')
        cases = (
            ('alone', HUGE_ID, []),
            ('next to the id of an object', f'{self.a1.pk},{HUGE_ID}', [self.a1.pk]),
            ('negative, alone', f'-{HUGE_ID}', []),
            ('negative, next to the ids of two objects', f'{self.a1.pk},-{HUGE_ID},{self.a2.pk}', [self.a1.pk, self.a2.pk]),
        )
        for case, selected, expected in cases:
            with self.subTest(case=case):
                self._assert_csv_ids(self._selected(AUTHOR_LIST, selected), expected,
                                     'a number the column cannot hold is an id no object owns')

    def test_selection_ids_no_object_owns_export_only_the_objects_that_exist(self):
        """AC-8985-9: com id sem objeto o arquivo traz só os que existem (só o cabeçalho se nenhum), sem erro, nas duas chaves."""
        absent_uuid = str(uuid.uuid4())
        cases = (
            ('author', AUTHOR_LIST, 'id no object owns, alone', MISSING_ID, []),
            ('author', AUTHOR_LIST, 'id no object owns, next to an object', f'{self.a1.pk},{MISSING_ID}', [self.a1.pk]),
            ('author', AUTHOR_LIST, 'two objects and an id no object owns', f'{self.a1.pk},{MISSING_ID},{self.a2.pk}',
             [self.a1.pk, self.a2.pk]),
            ('tag', TAG_LIST, 'UUID no object owns, alone', absent_uuid, []),
            ('tag', TAG_LIST, 'UUID no object owns, next to an object', f'{self.t1.pk},{absent_uuid}', [self.t1.pk]),
        )
        for model, list_url, case, selected, expected in cases:
            with self.subTest(model=model, case=case):
                self._assert_csv_ids(self._selected(list_url, selected), expected,
                                     'only the objects that exist may be exported')

    def test_selection_ids_the_key_converts_export_exactly_those_objects(self):
        """AC-8985-10: itens que a chave converte (também com espaço ou sinal; UUID) de objetos visíveis exportam só esses."""
        a1, a2 = self.a1.pk, self.a2.pk
        cases = (
            ('author', AUTHOR_LIST, 'one id', str(a1), [a1]),
            ('author', AUTHOR_LIST, 'two ids', f'{a1},{a2}', [a1, a2]),
            ('author', AUTHOR_LIST, 'space after the comma', f'{a1}, {a2}', [a1, a2]),
            ('author', AUTHOR_LIST, 'sign before the id', f'+{a1}', [a1]),
            ('tag', TAG_LIST, 'one UUID', str(self.t1.pk), [self.t1.pk]),
            ('tag', TAG_LIST, 'two UUIDs', f'{self.t1.pk},{self.t2.pk}', [self.t1.pk, self.t2.pk]),
        )
        for model, list_url, case, selected, expected in cases:
            with self.subTest(model=model, case=case):
                self._assert_csv_ids(self._selected(list_url, selected), expected,
                                     'only the selected objects may be exported, in the format asked')

    def test_object_the_admin_does_not_show_to_the_user_is_never_exported(self):
        """AC-8985-11: o objeto que existe e o admin não mostra àquele usuário nunca é exportado, com ou sem `_select_across`."""
        mine = [self.na1.pk, self.na2.pk]
        # pré-condições positivas: a nota do outro dono existe e o dono dela a vê; a lista corrente do ator traz só as dele
        self.assertTrue(Note.objects.filter(pk=self.nb.pk).exists(), msg='the other owner\'s note must exist')
        self._assert_csv_ids(self._export(self.owner_b, NOTE_LIST, format='0'), [self.nb.pk],
                             'control: the owner of the note sees it')
        self._assert_csv_ids(self._export(self.owner_a, NOTE_LIST, format='0'), mine,
                             'control: the actor\'s own list holds only his notes')
        cases = (
            ('selection with a note of each owner', self._selected(NOTE_LIST, f'{self.na1.pk},{self.nb.pk}', self.owner_a),
             [self.na1.pk]),
            ('selection of the other owner\'s note only', self._selected(NOTE_LIST, str(self.nb.pk), self.owner_a), []),
            ('every record, with the other owner\'s note selected',
             self._selected(NOTE_LIST, str(self.nb.pk), self.owner_a, _select_across='1'), mine),
            ('every record, with a selection of the actor\'s own note',
             self._selected(NOTE_LIST, str(self.na1.pk), self.owner_a, _select_across='1'), mine),
            ('scope all', self._export(self.owner_a, NOTE_LIST, format='0', scope='all'), mine),
        )
        for case, response, expected in cases:
            with self.subTest(case=case):
                self._assert_csv_ids(response, expected, 'the export must never reach a note the admin does not show')

    def test_select_across_ignores_a_non_empty_selection(self):
        """AC-8985-12: com `_select_across=1` e seleção não vazia os itens são ignorados e exporta todos que o admin mostra."""
        cases = (
            ('author', AUTHOR_LIST, 'an id', str(self.a1.pk), self._author_ids()),
            ('author', AUTHOR_LIST, 'text the key does not convert', 'abc', self._author_ids()),
            ('tag', TAG_LIST, 'a UUID', str(self.t1.pk), [self.t1.pk, self.t2.pk]),
            ('tag', TAG_LIST, 'text that is not a UUID', 'abc', [self.t1.pk, self.t2.pk]),
        )
        for model, list_url, case, selected, expected in cases:
            with self.subTest(model=model, case=case):
                self._assert_csv_ids(self._selected(list_url, selected, _select_across='1'), expected,
                                     'every record must be exported whatever the selection says')

    def test_scopes_other_than_selected_ignore_the_selection(self):
        """AC-8985-13: `all`, `all` filtrado, `header_only` e outro valor de `scope` ignoram a seleção, até com item inválido."""
        everyone = self._author_ids()
        self.assertGreater(len(everyone), 1, msg='the comparison needs more than one object')
        scopes = (
            ('all', {'scope': 'all'}, everyone),
            ('all, filtered', {'scope': 'all', 'queryset_all': 'filtered'}, everyone),
            ('header only', {'scope': 'header_only'}, []),
            ('another value', {'scope': 'zzz'}, everyone),
        )
        selections = (('absent', {}), ('an id', {'_selected_actions': str(self.a1.pk)}),
                      ('text the key does not convert', {'_selected_actions': 'abc'}))
        for scope_case, scope_params, expected in scopes:
            for selection_case, selection_params in selections:
                with self.subTest(scope=scope_case, selection=selection_case):
                    response = self._export(self.viewer, AUTHOR_LIST, format='0', **scope_params, **selection_params)
                    self._assert_csv_ids(response, expected, 'the selection must be ignored outside scope=selected')

    def test_e_mail_delivery_gets_only_the_selected_objects_and_goes_back_to_the_list(self):
        """AC-8985-17: o envio por e-mail com seleção válida entrega só os selecionados (um envio) e responde 302 à lista."""
        with mock.patch.object(ExportPlugin, 'send_mail') as sendmail:
            response = self._selected(AUTHOR_LIST, f'{self.a1.pk},{self.a3.pk}', **{'result-action': 'sendmail'})
            self.assertEqual(response.status_code, 302, msg='the delivery answers with a redirect')
            self.assertEqual(response['Location'], AUTHOR_LIST, msg='the redirect goes back to the list itself')
            self.assertEqual(sendmail.call_count, 1, msg='exactly one delivery')
            delivered = sorted(obj.pk for obj in sendmail.call_args.kwargs['queryset'])
            self.assertEqual(delivered, sorted([self.a1.pk, self.a3.pk]), msg='the delivery must get only the selected objects')


class ExportScopeFilterTests(_ExportMixin, TestCase):
    """The scopes of the export that read the list (``queryset_all=filtered`` and any other ``scope``) under a real search.

    Added after the verifier of #8985 found that, on a list with no filter and a single page, ``all``, ``all`` filtered and
    any other ``scope`` return the same rows, so a swap of the three querysets would pass. The ``Ranked`` admin declares
    ``search_fields``, so ``?_q_=alpha`` narrows its list; its formats are XLS and XLSX, so the file is read back as XLSX.
    """

    @classmethod
    def setUpTestData(cls):
        cls.viewer = _staff('raw-scope-viewer', 'view_ranked')
        cls.r1 = Ranked.objects.create(name='alpha one', position=1)
        cls.r2 = Ranked.objects.create(name='alpha two', position=2)
        cls.r3 = Ranked.objects.create(name='beta', position=3)

    def _assert_xlsx_ids(self, response, expected, msg):
        """200 with an XLSX file whose rows are exactly the ``expected`` keys (the header must be there even with no rows)."""
        self._assert_served(response, XLSX_TYPE, msg)
        dataset = tablib.Dataset().load(response.content, format='xlsx')
        self.assertIn('id', dataset.headers or [], msg=f'{msg}: the file must carry the header row')
        self.assertEqual(sorted(int(pk) for pk in dataset['id']), sorted(expected), msg=msg)

    def test_filtered_and_current_list_scopes_follow_the_search_of_the_list(self):
        """AC-8985-13: com busca na lista, `all` filtrado e outro valor de `scope` exportam a lista pesquisada, e `all` o admin."""
        everyone = [self.r1.pk, self.r2.pk, self.r3.pk]
        searched = [self.r1.pk, self.r2.pk]
        # pré-condição: a busca estreita a lista; sem isso os escopos abaixo dariam o mesmo e o teste não provaria nada
        self.assertLess(len(searched), len(everyone), msg='the search must narrow the list')
        # controle positivo: sem busca a lista corrente é a lista inteira (o `scope` que nenhum ramo conhece)
        self._assert_xlsx_ids(self._export(self.viewer, RANKED_LIST, format='1', scope='zzz'), everyone,
                              'control: without a search the current list is the whole list')
        scopes = (
            # `get_list_queryset()` aplica a busca; `queryset()` do admin não (importexport.py, `get_export_queryset`)
            ('all, filtered', {'scope': 'all', 'queryset_all': 'filtered'}, searched),
            ('all, not filtered', {'scope': 'all'}, everyone),
            # o ramo final lê as linhas da lista corrente (`context['results']`), que a busca já estreitou
            ('another value', {'scope': 'zzz'}, searched),
            ('header only', {'scope': 'header_only'}, []),
        )
        # a seleção é ignorada em todos: um id que a busca não acha e um texto que a chave não converte
        selections = (('absent', {}), ('an id the search does not find', {'_selected_actions': str(self.r3.pk)}),
                      ('text the key does not convert', {'_selected_actions': 'abc'}))
        for scope_case, scope_params, expected in scopes:
            for selection_case, selection_params in selections:
                with self.subTest(scope=scope_case, selection=selection_case):
                    response = self._export(self.viewer, RANKED_LIST, format='1', _q_='alpha', **scope_params,
                                            **selection_params)
                    self._assert_xlsx_ids(response, expected, 'the scope must read the list as the criterion says')


class ExportScopePagesTests(_ExportMixin, TestCase):
    """The filtered list and the current list tell apart only when the list has more than one page.

    ``queryset_all=filtered`` exports ``get_list_queryset()`` (the whole filtered list, every page) and any other ``scope``
    exports the rows of the current page (``context['results']`` iterates ``result_list``, a page slice when the list is
    multi-page: ``xadmin/views/list.py:202-209``). Added after the second pass of the verifier of #8985: with a single page
    the two branches return the same rows, so a swap between them would pass. The page size is the one the list really uses,
    so no number is fixed here.
    """

    @classmethod
    def setUpTestData(cls):
        cls.viewer = _staff('raw-pages-viewer', 'view_ranked')
        # o `RankedAdmin` não declara `list_per_page`: vale o da view; o getattr lê o do admin se um dia ele o declarar
        cls.per_page = getattr(site._registry[Ranked], 'list_per_page', ListAdminView.list_per_page)
        # `per_page + 5` linhas que a busca `alpha` acha (a 1ª página cheia e a 2ª com 5) e 2 que ela não acha
        Ranked.objects.bulk_create([Ranked(name=f'alpha {n:03d}', position=n) for n in range(cls.per_page + 5)])
        Ranked.objects.bulk_create([Ranked(name=f'beta {n}', position=n) for n in range(2)])
        cls.alpha = set(Ranked.objects.filter(name__startswith='alpha').values_list('pk', flat=True))
        cls.beta = set(Ranked.objects.filter(name__startswith='beta').values_list('pk', flat=True))

    def _exported_ids(self, **params):
        """The keys of the rows of an XLSX export of the list searched with ``alpha`` (no row may come twice)."""
        response = self._export(self.viewer, RANKED_LIST, format='1', _q_='alpha', **params)
        self._assert_served(response, XLSX_TYPE, f'export with {params}')
        dataset = tablib.Dataset().load(response.content, format='xlsx')
        self.assertIn('id', dataset.headers or [], msg=f'export with {params}: the file must carry the header row')
        ids = [int(pk) for pk in dataset['id']]
        self.assertEqual(len(ids), len(set(ids)), msg=f'export with {params}: no row may be exported twice')
        return ids

    def test_filtered_scope_exports_every_page_and_the_other_scopes_only_the_current_one(self):
        """AC-8985-13: com a lista em duas páginas, `all` filtrado exporta as duas e outro valor de `scope`, só a corrente."""
        per_page = self.per_page
        # pré-condições: a lista filtrada tem duas páginas (a 1ª cheia, a 2ª com 5 linhas) e a busca deixa as beta de fora;
        # sem isso os ramos dariam o mesmo e o teste não provaria nada
        self.assertEqual(len(self.alpha), per_page + 5, msg='the search must find more rows than a page holds')
        self.assertEqual(len(self.beta), 2, msg='the search must leave two rows out')
        # a seleção é ignorada: um id que o resultado não traz (uma `beta`) e um texto que a chave não converte
        selections = (('absent', {}), ('an id outside the result', {'_selected_actions': str(min(self.beta))}),
                      ('text the key does not convert', {'_selected_actions': 'abc'}))
        for selection_case, selection_params in selections:
            with self.subTest(scope='all, filtered', selection=selection_case):
                ids = self._exported_ids(scope='all', queryset_all='filtered', **selection_params)
                self.assertEqual(set(ids), self.alpha, msg='the filtered scope must export the whole filtered list')
                self.assertEqual(len(ids), per_page + 5, msg='the filtered scope must export the rows of every page')
            with self.subTest(scope='another value', selection=selection_case):
                ids = self._exported_ids(scope='zzz', **selection_params)
                self.assertEqual(len(ids), per_page, msg='another scope must export only the rows of the current page')
                self.assertTrue(set(ids) < self.alpha, msg='the page must be rows the search found, and not all of them')
        # a 2ª página (`p` conta de 0, list.py:153): as linhas que faltam, sem repetir as da 1ª
        first, second = self._exported_ids(scope='zzz'), self._exported_ids(scope='zzz', p='1')
        self.assertEqual(len(second), 5, msg='the second page must hold the five rows left')
        self.assertFalse(set(first) & set(second), msg='the two pages must not share a row')
        self.assertEqual(set(first) | set(second), self.alpha, msg='the two pages must hold the whole filtered list')
        # `all` sem `queryset_all`: o `queryset()` do admin, sem busca e sem página
        self.assertEqual(set(self._exported_ids(scope='all')), self.alpha | self.beta,
                         msg='scope all must export every row of the admin')
        # `header_only` não exporta linha nenhuma
        self.assertEqual(self._exported_ids(scope='header_only'), [], msg='header_only must export no row')


class ExportFormatTests(_ExportMixin, TestCase):
    """The ``format`` of the export (and the old name ``file_format``), and the warnings that come before it."""

    @classmethod
    def setUpTestData(cls):
        cls.viewer = _staff('raw-format-viewer', 'view_author', 'view_tag', 'view_ranked')
        cls.blind = _staff('raw-format-blind')
        cls.a1 = Author.objects.create(name='a1')
        cls.a2 = Author.objects.create(name='a2')
        cls.r1 = Ranked.objects.create(name='r1', position=10)

    def _formats_of_the_author_admin(self):
        """How many formats the admin of ``Author`` offers (the default list): the first index out of range."""
        offered = len([f for f in DEFAULT_FORMATS if f().can_export()])
        # o admin restrito do `Ranked` oferece 2: o índice `2` só está fora do intervalo nele, e o número abaixo, nos dois
        self.assertGreater(offered, 2, msg='the Author admin must offer more formats than the restricted Ranked admin')
        return offered

    def test_warnings_stay_302_and_come_before_the_format_check(self):
        """AC-8985-14: sem `format`, com `format` vazio ou com seleção vazia o aviso em 302 vem antes de qualquer 400."""
        cases = (
            ('no format at all', {}),
            ('no format, scope all', {'scope': 'all'}),
            ('empty format and file_format', {'format': '', 'file_format': '', 'scope': 'all'}),
            ('empty selection', {'format': '0', 'scope': 'selected', '_selected_actions': ''}),
            ('selection of spaces', {'format': '0', 'scope': 'selected', '_selected_actions': '  '}),
            ('empty selection with _select_across=1', {'format': '0', 'scope': 'selected', '_selected_actions': '',
                                                        '_select_across': '1'}),
            ('spaces with _select_across=1', {'format': '0', 'scope': 'selected', '_selected_actions': ' ',
                                              '_select_across': '1'}),
            # o aviso da seleção vazia vence o 400 do `format`: a seleção vazia não é entrada malformada
            ('empty selection with an invalid format', {'format': 'abc', 'scope': 'selected', '_selected_actions': ''}),
            ('spaces with an invalid format and _select_across=1', {'format': 'abc', 'scope': 'selected',
                                                                    '_selected_actions': ' ', '_select_across': '1'}),
        )
        for case, params in cases:
            with self.subTest(case=case):
                self._assert_warned(self._export(self.viewer, AUTHOR_LIST, **params),
                                    'a warning must come before the format is checked')

    def test_format_that_is_not_an_available_index_answers_400_and_exports_nothing(self):
        """AC-8985-15: o `format` (ou `file_format`) que não é índice de formato do admin dá 400, sem exportar."""
        first_out = self._formats_of_the_author_admin()
        # controle positivo: o último índice válido exporta, pelo mesmo ator e pela mesma rota
        self._assert_served(self._export(self.viewer, AUTHOR_LIST, format=str(first_out - 1), scope='all'), 'text/html',
                            'control: the last valid index must export')
        cases = (
            ('text', {'format': 'abc', 'scope': 'all'}),
            ('decimal number', {'format': '1.5', 'scope': 'all'}),
            ('the first index out of range', {'format': str(first_out), 'scope': 'all'}),
            ('a far index out of range', {'format': '99', 'scope': 'all'}),
            ('negative number', {'format': '-1', 'scope': 'all'}),
            ('file_format as text', {'file_format': 'abc', 'scope': 'all'}),
            ('file_format negative', {'file_format': '-1', 'scope': 'all'}),
            ('file_format out of range', {'file_format': str(first_out), 'scope': 'all'}),
            ('text next to a valid selection', {'format': 'abc', 'scope': 'selected', '_selected_actions': str(self.a1.pk)}),
        )
        for case, params in cases:
            with self.subTest(case=case):
                self._assert_refused(self._export(self.viewer, AUTHOR_LIST, **params),
                                     'a format that is not an available index must answer 400')

    def test_format_index_is_checked_against_the_formats_of_the_admin(self):
        """AC-8985-15: o índice vale contra a lista do admin (o `Ranked` oferece só XLS e XLSX): o `2` e o `-1` dão 400."""
        # controle positivo: o último índice dessa lista exporta em XLSX, então o 400 abaixo é do intervalo do admin
        self._assert_served(self._export(self.viewer, RANKED_LIST, format='1', scope='all'), XLSX_TYPE,
                            'control: the last index of the restricted list must export')
        for case, value in (('the first index out of the restricted list', '2'), ('negative number', '-1')):
            with self.subTest(case=case):
                self._assert_refused(self._export(self.viewer, RANKED_LIST, format=value, scope='all'),
                                     'the index must be checked against the formats of the admin')

    def test_invalid_format_is_refused_before_the_e_mail_delivery(self):
        """AC-8985-15: o `format` que não é índice dá 400 também no envio por e-mail, sem enviar."""
        with mock.patch.object(ExportPlugin, 'send_mail') as sendmail:
            # controle positivo: com o `format` válido o envio chega ao dublê, que está ligado
            self._export(self.viewer, AUTHOR_LIST, format='0', scope='all', **{'result-action': 'sendmail'})
            self.assertEqual(sendmail.call_count, 1, msg='control: a valid format must reach the delivery')
            sendmail.reset_mock()
            for case, value in (('text', 'abc'), ('negative number', '-1')):
                with self.subTest(case=case):
                    self._assert_refused(self._export(self.viewer, AUTHOR_LIST, format=value, scope='all',
                                                      **{'result-action': 'sendmail'}),
                                         'an invalid format must answer 400 instead of sending')
                    self.assertEqual(sendmail.call_count, 0, msg='nothing may be sent')

    def test_valid_format_index_keeps_exporting_in_the_format_asked(self):
        """AC-8985-16: o `format` (ou o `file_format`) que é índice de um formato exporta no formato pedido; o `format` vale."""
        last = self._formats_of_the_author_admin() - 1
        cases = (
            ('format 0', {'format': '0'}, CSV_TYPE),
            ('format 1', {'format': '1'}, XLS_TYPE),
            ('format with a space', {'format': ' 0'}, CSV_TYPE),
            ('the last index', {'format': str(last)}, 'text/html'),
            ('file_format alone', {'file_format': '0'}, CSV_TYPE),
            ('empty format, file_format', {'format': '', 'file_format': '0'}, CSV_TYPE),
            ('format wins over file_format', {'format': '1', 'file_format': 'abc'}, XLS_TYPE),
        )
        for case, params, content_type in cases:
            with self.subTest(case=case):
                self._assert_served(self._export(self.viewer, AUTHOR_LIST, scope='all', **params), content_type,
                                    'a valid index must export in the format asked')

    def test_valid_format_index_of_a_restricted_admin_keeps_exporting_in_its_format(self):
        """AC-8985-16: no admin de `formats` restrito o índice 0 exporta em XLS e o 1 em XLSX."""
        for case, value, content_type in (('index 0', '0', XLS_TYPE), ('index 1', '1', XLSX_TYPE)):
            with self.subTest(case=case):
                self._assert_served(self._export(self.viewer, RANKED_LIST, format=value, scope='all'), content_type,
                                    'the index must be read against the formats of the admin')

    def test_user_who_cannot_view_the_list_gets_403_whatever_the_input(self):
        """AC-8985-18 (controle): quem não pode ver a lista recebe 403 com `format` ou item de `_selected_actions` inválido."""
        # controle positivo: quem pode ver exporta a mesma rota com o mesmo formato válido
        self._assert_served(self._export(self.viewer, AUTHOR_LIST, format='0', scope='all'), CSV_TYPE,
                            'control: who can view the list must export')
        cases = (
            ('invalid format', {'format': 'abc', 'scope': 'all'}),
            ('negative format', {'format': '-1', 'scope': 'all'}),
            ('invalid item', {'format': '0', 'scope': 'selected', '_selected_actions': 'abc'}),
        )
        for case, params in cases:
            with self.subTest(case=case):
                response = self._export(self.blind, AUTHOR_LIST, **params)
                self.assertEqual(response.status_code, 403, msg='who cannot view the list gets 403, not 400')
                self.assertFalse(response.has_header('Content-Disposition'), msg='no file may be served')


class SiteGateTests(_ClientMixin, TestCase):
    """The gate of the site answers before the three routes: the new 400 is not reachable by who is not in."""

    @classmethod
    def setUpTestData(cls):
        cls.plain = User.objects.create_user('raw-gate-plain', 'raw-gate-plain@example.com', 'not-a-real-password')
        cls.author = Author.objects.create(name='gate-author')

    def test_visitor_and_non_staff_user_get_the_site_gate_on_the_three_routes(self):
        """AC-8985-19 (controle): o visitante e o usuário fora da equipe seguem no portão do site, nunca no 400, nas 3 rotas."""
        # (método, rota, a requisição que a tela manda, a requisição inválida do critério)
        routes = {
            'quick edit': ('get', _patch_url(AUTHOR_LIST, self.author.pk), {'fields': 'name'}, {}),
            'bookmark': ('post', BOOKMARK_URL, {'title': 'x'}, {}),
            'export': ('get', AUTHOR_LIST, {'_action_': 'export', 'format': '0', 'scope': 'all'},
                       {'_action_': 'export', 'format': 'abc', 'scope': 'selected', '_selected_actions': 'abc'}),
        }
        author_before, bookmarks_before = _table(Author), Bookmark.objects.count()
        for who, client in {'visitor': self._client_for(), 'non-staff': self._client_for(self.plain)}.items():
            for route, (method, url, valid, invalid) in routes.items():
                with self.subTest(who=who, route=route):
                    gate = getattr(client, method)(url, valid)
                    response = getattr(client, method)(url, invalid)
                    self.assertEqual(gate.status_code, 200, msg='the gate is the login page')
                    self.assertEqual(response.status_code, gate.status_code,
                                     msg='the invalid input must get the answer the gate gives')
                    self.assertFalse(response.has_header('Location'), msg='the gate does not redirect')
                    self.assertFalse(response.has_header('Content-Disposition'), msg='no file may be served')
                    self.assertEqual(_table(Author), author_before)
                    self.assertEqual(Bookmark.objects.count(), bookmarks_before, msg='no bookmark may be created')
