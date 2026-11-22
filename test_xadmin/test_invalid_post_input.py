# coding=utf-8
"""A forged POST with an invalid id, or an invalid user setting, answers 400 or 404, never 500. See #8930.

Five views of the package read ``request.POST`` without a form and used the raw value:
the batch action of the list (``_selected_action``), the dashboard (``id``), the sortable
list (``order[]``), the version comparison (``version_a``/``version_b``) and the user
setting (``key`` and ``value``). An authenticated client that sent text where the key
field wants a number, the NUL byte, a missing parameter, a key longer than the column or
a key that already has two rows got a 500. These tests pin the answer a client should
get: 400 for the input the key field does not convert, 404 for an id the key accepts but
no object owns, and a normal answer for every valid input (the ``(controle)`` tests).

The client is built with ``raise_request_exception=False`` so a 500 shows up as a status
to compare, and the red fails on the assertion instead of erroring on the exception.

The bytes of the ids below are what the key field decides, not what ``int()`` accepts:
``' 5'``, ``'+5'`` and ``'1_0'`` are valid numbers and must not get a 400.

    python runtests.py test_xadmin.test_invalid_post_input
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_invalid_post_input'
"""
import json
import uuid
from unittest import mock

import reversion
from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase

from xadmin.models import UserSettings, UserWidget
from xadmin.plugins.sortablelist import SaveOrderView

from test_xadmin.fixtureapp.models import Author, Ranked, Tag

MISSING_ID = '987654321'
# maior que qualquer inteiro de 64 bits: o campo da chave aceita, o intervalo do banco não
HUGE_ID = '1' + '0' * 29
NUL_ID = '1\x00'
INVALID_IDS = ('abc', '', NUL_ID)


def _staff(username, *codenames):
    """Staff user (xadmin only lets staff in) with exactly the given permissions."""
    user = User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password',
                                    is_staff=True)
    if codenames:
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


class _ClientMixin:
    """A client that turns an unhandled exception into a 500 response instead of raising."""

    def _client_for(self, user=None):
        client = Client(raise_request_exception=False)
        if user is not None:
            client.force_login(user)
        return client


def _names(model):
    return dict(model.objects.values_list('pk', 'name'))


class BatchActionTests(_ClientMixin, TestCase):
    """The batch action of the list (``ActionPlugin.post_response``) on the ``Ranked`` and ``Tag`` admins."""

    RANKED_URL = '/xadmin/xadmin_fixture/ranked/'
    TAG_URL = '/xadmin/xadmin_fixture/tag/'

    @classmethod
    def setUpTestData(cls):
        cls.lister = _staff('batch-lister', 'view_ranked', 'change_ranked', 'view_tag', 'change_tag')
        cls.first = Ranked.objects.create(name='first', position=10)
        cls.second = Ranked.objects.create(name='second', position=20)
        cls.tag_a = Tag.objects.create(name='tag-a')
        cls.tag_b = Tag.objects.create(name='tag-b')

    def _post(self, url, ids, action='mark_seen', **extra):
        data = {'action': action, '_selected_action': list(ids)}
        data.update(extra)
        return self._client_for(self.lister).post(url, data)

    def test_invalid_selected_id_answers_400_and_runs_the_action_on_no_object(self):
        """AC-8930-1: id selecionado que a chave não converte dá 400 e a ação não roda em objeto nenhum."""
        ranked_before, tags_before = _names(Ranked), _names(Tag)
        for bad in INVALID_IDS:
            with self.subTest(key='integer', bad=bad):
                response = self._post(self.RANKED_URL, [self.first.pk, bad])
                self.assertEqual(response.status_code, 400, msg='an id the key does not convert must answer 400')
                self.assertEqual(_names(Ranked), ranked_before, msg='the action must run on no object')
            with self.subTest(key='uuid', bad=bad):
                response = self._post(self.TAG_URL, [str(self.tag_a.pk), bad])
                self.assertEqual(response.status_code, 400, msg='a text that is not a UUID must answer 400')
                self.assertEqual(_names(Tag), tags_before, msg='the action must run on no object')

    def test_numbers_that_int_accepts_are_not_a_400(self):
        """AC-8930-1 (controle): ' 5', '+5' e '1_0' são números que o campo converte: não dão 400."""
        for good in (' 5', '+5', '1_0'):
            with self.subTest(good=good):
                response = self._post(self.RANKED_URL, [good])
                self.assertEqual(response.status_code, 302)

    def test_unknown_action_with_an_invalid_id_keeps_answering_200(self):
        """AC-8930-1 (controle): a ação que não existe, com id inválido, segue respondendo 200 com a mensagem."""
        response = self._post(self.RANKED_URL, ['abc'], action='no_such_action')
        self.assertEqual(response.status_code, 200)

    def test_valid_selection_runs_the_action_on_the_selected_rows_of_the_list(self):
        """AC-8930-9 (controle): a seleção válida atua só nos selecionados e um id sem objeto não é erro."""
        ranked_before, tags_before = _names(Ranked), _names(Tag)
        response = self._post(self.RANKED_URL, [self.first.pk, MISSING_ID])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.RANKED_URL)
        self.assertEqual(_names(Ranked), {**ranked_before, self.first.pk: 'seen'})
        # chave UUID: a seleção válida, e um UUID que não é de objeto nenhum, também funcionam
        response = self._post(self.TAG_URL, [str(self.tag_a.pk), str(uuid.uuid4())])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_names(Tag), {**tags_before, self.tag_a.pk: 'seen'})

    def test_select_across_ignores_the_selected_ids(self):
        """AC-8930-10 (controle): selecionando todos os registros a ação atua em todos, sem conferir os ids."""
        response = self._post(self.RANKED_URL, ['abc'], select_across='1')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(_names(Ranked).values()), {'seen'})

    def test_selected_id_the_list_leaves_out_is_not_acted_on(self):
        """AC-8930-9 (controle): a ação atua só nos objetos da lista: o id selecionado que a lista exclui não muda."""
        # `?_q_=first` estreita a lista a `first` (search_fields do admin Ranked); o `second` está fora dela
        narrowed = f'{self.RANKED_URL}?_q_=first'
        response = self._post(narrowed, [self.first.pk, self.second.pk])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_names(Ranked), {self.first.pk: 'seen', self.second.pk: 'second'})
        # pré-condição positiva: sem a busca a mesma ação atua no `second`, então ele só ficou parado por estar fora da lista
        response = self._post(self.RANKED_URL, [self.second.pk])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_names(Ranked), {self.first.pk: 'seen', self.second.pk: 'seen'})

    def test_select_across_acts_only_on_the_rows_of_the_list(self):
        """AC-8930-10 (controle): selecionando todos os registros, a ação atua em todos os da lista, não do modelo."""
        response = self._post(f'{self.RANKED_URL}?_q_=first', ['abc'], select_across='1')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_names(Ranked), {self.first.pk: 'seen', self.second.pk: 'second'})

    def test_response_of_the_action_is_the_response_of_the_post(self):
        """AC-8930-9 (controle): a ação que devolve uma resposta tem essa resposta devolvida pelo POST, só nos selecionados."""
        response = self._post(self.RANKED_URL, [self.first.pk, MISSING_ID], action='reply')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'replied 1')

    def test_id_out_of_the_database_range_is_an_id_with_no_object(self):
        """AC-8930-17: o número que a chave converte mas o banco não comporta é um id sem objeto, não um erro."""
        before = _names(Ranked)
        response = self._post(self.RANKED_URL, [self.first.pk, HUGE_ID])
        self.assertEqual(response.status_code, 302, msg='an out-of-range number must not break the action')
        self.assertEqual(_names(Ranked), {**before, self.first.pk: 'seen'})


class GateTests(_ClientMixin, TestCase):
    """The gates answer before any view code reads the id: the invalid id changes nothing for them."""

    @classmethod
    def setUpTestData(cls):
        cls.plain = User.objects.create_user('gate-plain', 'gate-plain@example.com', 'not-a-real-password')
        cls.blind = _staff('gate-blind')
        cls.owner = _staff('gate-owner')
        cls.first = Ranked.objects.create(name='first', position=10)
        cls.widget = UserWidget.objects.create(
            user=cls.owner, page_id='home', widget_type='html',
            value=json.dumps({'title': 'before', 'content': '<p>before</p>'}))

    def _cases(self):
        """(rota, entrada inválida, entrada válida) das três rotas que leem um id do POST."""
        return [
            ('/xadmin/xadmin_fixture/ranked/',
             {'action': 'mark_seen', '_selected_action': ['abc']},
             {'action': 'mark_seen', '_selected_action': [self.first.pk]}),
            ('/xadmin/', {'id': 'abc'}, {'id': self.widget.pk}),
            ('/xadmin/xadmin_fixture/ranked/save-order/',
             {'order[]': ['abc']}, {'order[]': [self.first.pk]}),
        ]

    def test_visitor_and_non_staff_user_stay_at_the_site_gate(self):
        """AC-8930-18 (controle): visitante e usuário fora da equipe seguem no portão do site e nada é gravado."""
        clients = {'visitor': self._client_for(), 'non-staff': self._client_for(self.plain)}
        ranked_before = list(Ranked.objects.values_list('pk', 'name', 'position'))
        widgets_before = list(UserWidget.objects.values_list('pk', 'value'))
        for who, client in clients.items():
            for url, invalid, valid in self._cases():
                with self.subTest(who=who, url=url):
                    gate = client.post(url, valid).status_code
                    response = client.post(url, invalid)
                    self.assertEqual(response.status_code, gate,
                                     msg='the invalid id must get the answer the gate gives today')
                    self.assertNotIn(response.status_code, (400, 500), msg='the gate answers before the view')
                    self.assertEqual(list(Ranked.objects.values_list('pk', 'name', 'position')), ranked_before)
                    self.assertEqual(list(UserWidget.objects.values_list('pk', 'value')), widgets_before)

    def test_staff_user_who_cannot_see_the_list_gets_403(self):
        """AC-8930-19 (controle): usuário da equipe que não pode ver a lista segue com 403 e a ação não roda."""
        before = _names(Ranked)
        response = self._client_for(self.blind).post(
            '/xadmin/xadmin_fixture/ranked/', {'action': 'mark_seen', '_selected_action': ['abc']})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(_names(Ranked), before)


class DashboardTests(_ClientMixin, TestCase):
    """``Dashboard.post`` on the main dashboard (page ``home``) of a staff user without any permission."""

    URL = '/xadmin/'

    @classmethod
    def setUpTestData(cls):
        cls.owner = _staff('dash-owner')
        cls.other = _staff('dash-other')
        cls.widget = UserWidget.objects.create(
            user=cls.owner, page_id='home', widget_type='html',
            value=json.dumps({'title': 'before', 'content': '<p>before</p>'}))
        cls.foreign = UserWidget.objects.create(
            user=cls.other, page_id='home', widget_type='html',
            value=json.dumps({'title': 'theirs', 'content': '<p>theirs</p>'}))

    def _widgets(self):
        return list(UserWidget.objects.order_by('pk').values_list('pk', 'user_id', 'value'))

    def _post(self, data):
        return self._client_for(self.owner).post(self.URL, data)

    def test_invalid_widget_id_answers_400_on_save_and_on_delete(self):
        """AC-8930-2: o `id` que a chave do widget não converte dá 400 ao salvar e ao excluir, sem mexer em widget."""
        before = self._widgets()
        for bad in INVALID_IDS:
            for delete in (False, True):
                with self.subTest(bad=bad, delete=delete):
                    data = {'id': bad, 'title': 'x', 'content': 'y'}
                    if delete:
                        data['_delete'] = 'on'
                    response = self._post(data)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(self._widgets(), before, msg='no widget may change')

    def test_saving_an_id_that_is_not_a_widget_of_the_user_answers_404(self):
        """AC-8930-3: salvar um id que a chave aceita mas que não é de widget do usuário dá 404 e não grava."""
        before = self._widgets()
        for target in (MISSING_ID, str(self.foreign.pk), HUGE_ID):
            with self.subTest(target=target):
                response = self._post({'id': target, 'title': 'x', 'content': 'y'})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(self._widgets(), before, msg='nothing may be saved')

    def test_saving_an_own_html_widget_keeps_working(self):
        """AC-8930-11 (controle): salvar o próprio widget HTML com dados válidos grava e responde o painel."""
        response = self._post({'id': self.widget.pk, 'title': 'after', 'content': '<p>after</p>'})
        self.assertEqual(response.status_code, 200)
        value = json.loads(UserWidget.objects.get(pk=self.widget.pk).value)
        self.assertEqual((value['title'], value['content']), ('after', '<p>after</p>'))

    def test_deleting_an_own_widget_keeps_working(self):
        """AC-8930-12 (controle): excluir o próprio widget remove só ele e responde o painel."""
        response = self._post({'id': self.widget.pk, '_delete': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([w[0] for w in self._widgets()], [self.foreign.pk])

    def test_deleting_a_numeric_id_with_no_widget_keeps_answering_the_dashboard(self):
        """AC-8930-13 (controle): excluir um id numérico sem widget responde o painel e não apaga nada."""
        before = self._widgets()
        response = self._post({'id': MISSING_ID, '_delete': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._widgets(), before)


class SaveOrderTests(_ClientMixin, TestCase):
    """``SaveOrderView`` (route ``save-order/``) on the ``Ranked`` admin, whose key is an integer."""

    URL = '/xadmin/xadmin_fixture/ranked/save-order/'

    @classmethod
    def setUpTestData(cls):
        cls.changer = _staff('order-changer', 'view_ranked', 'change_ranked')
        cls.r1 = Ranked.objects.create(name='r1', position=10)
        cls.r2 = Ranked.objects.create(name='r2', position=20)
        cls.r3 = Ranked.objects.create(name='r3', position=30)

    def _positions(self):
        return dict(Ranked.objects.values_list('pk', 'position'))

    def _post(self, order):
        return self._client_for(self.changer).post(self.URL, {'order[]': list(order)})

    def test_invalid_id_answers_400_and_changes_no_order(self):
        """AC-8930-4: ao menos um id que a chave não converte dá 400 e a ordem de ninguém muda."""
        before = self._positions()
        for bad in INVALID_IDS:
            # o válido que vem antes do inválido não pode ter sido gravado; o 400 é um `return` e não desfaz o `atomic`
            for order in ([self.r1.pk, bad], [MISSING_ID, bad], [bad, self.r1.pk]):
                with self.subTest(bad=bad, order=order):
                    response = self._post(order)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(self._positions(), before, msg='no order may change')

    def test_ids_the_key_accepts_but_no_object_owns_answer_404_and_change_no_order(self):
        """AC-8930-5: todos os ids aceitos e algum sem objeto dá 404 e a ordem de ninguém muda."""
        before = self._positions()
        for missing in (MISSING_ID, HUGE_ID):
            for order in ([self.r1.pk, missing], [missing, self.r1.pk]):
                with self.subTest(missing=missing, order=order):
                    response = self._post(order)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(self._positions(), before, msg='no order may change')

    def test_valid_order_is_saved_and_answers_an_empty_json_object(self):
        """AC-8930-14 (controle): a ordenação válida grava a nova ordem e responde 200 com um JSON vazio."""
        response = self._post([self.r3.pk, self.r1.pk, self.r2.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {})
        self.assertEqual(self._positions(), {self.r3.pk: 1, self.r1.pk: 2, self.r2.pk: 3})

    def test_save_order_hook_gets_each_converted_pk_and_position_in_order(self):
        """AC-8930-14 (controle): o `post` grava pelo gancho `save_order(pk, ordem)`, com o pk convertido e a posição de cada item."""
        # o gancho é público e o `plus_base` o sobrescreve para limpar o cache (plus_base/xpublique/adminx/__init__.py:108):
        # quem grava sem passar por ele deixa o cache sujo. O patch registra as chamadas e as deixa seguir
        with mock.patch.object(SaveOrderView, 'save_order', autospec=True,
                               side_effect=SaveOrderView.save_order) as hook:
            response = self._post([self.r3.pk, self.r1.pk, self.r2.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call.args[1:] for call in hook.call_args_list],
                         [(self.r3.pk, 1), (self.r1.pk, 2), (self.r2.pk, 3)])

    def test_inverse_order_counts_down_from_the_number_of_ids(self):
        """Robustez (sem critério): com `list_order_field_inverse` a primeira posição recebe o total e a última recebe 1."""
        # o `post` calcula o total a partir dos ids já convertidos (sortablelist.py, `total_order_objs`): este modo o usa
        with mock.patch.object(SaveOrderView, 'list_order_field_inverse', True):
            response = self._post([self.r3.pk, self.r1.pk, self.r2.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._positions(), {self.r3.pk: 3, self.r1.pk: 2, self.r2.pk: 1})

    def test_repeated_ids_of_existing_objects_are_not_a_404(self):
        """AC-8930-14 (controle): o mesmo objeto repetido na lista não é objeto inexistente."""
        response = self._post([self.r1.pk, self.r1.pk])
        self.assertEqual(response.status_code, 200)


class RevisionCompareTests(_ClientMixin, TestCase):
    """``RevisionListView.post`` (version comparison) on the reversion-enabled ``Author`` admin."""

    @classmethod
    def setUpTestData(cls):
        cls.editor = _staff('cmp-editor', 'add_author', 'change_author', 'delete_author', 'view_author')
        with reversion.create_revision():
            cls.author = Author.objects.create(name='first name')
        with reversion.create_revision():
            cls.author.name = 'second name'
            cls.author.save()

    def _post(self, version_a, version_b):
        url = f'/xadmin/xadmin_fixture/author/{self.author.pk}/revision/'
        return self._client_for(self.editor).post(url, {'version_a': version_a, 'version_b': version_b})

    def _version(self):
        from reversion.models import Version
        return Version.objects.get_for_object(self.author).first().pk

    def test_invalid_version_id_answers_400(self):
        """AC-8930-6: duas versões diferentes com ao menos uma que a chave não converte dá 400."""
        real = self._version()
        for bad in INVALID_IDS:
            for a, b in ((real, bad), (bad, real)):
                with self.subTest(a=a, b=b):
                    self.assertEqual(self._post(a, b).status_code, 400)

    def test_versions_the_key_accepts_but_no_version_owns_answer_404(self):
        """AC-8930-16 (controle): versões aceitas pela chave e sem versão (inclusive o número enorme) dão 404."""
        real = self._version()
        for missing in (MISSING_ID, HUGE_ID):
            with self.subTest(missing=missing):
                self.assertEqual(self._post(real, missing).status_code, 404)

    def test_the_same_invalid_id_twice_keeps_answering_the_message(self):
        """AC-8930-6 (controle): as duas versões iguais seguem respondendo 200 com a mensagem, antes da conversão."""
        self.assertEqual(self._post('abc', 'abc').status_code, 200)


class UserSettingTests(_ClientMixin, TestCase):
    """``UserSettingView.post`` (route ``settings/user``)."""

    URL = '/xadmin/settings/user'

    @classmethod
    def setUpTestData(cls):
        cls.user = _staff('settings-user')

    def _post(self, data):
        return self._client_for(self.user).post(self.URL, data)

    def test_missing_parameter_or_too_long_key_answers_400_and_stores_nothing(self):
        """AC-8930-7: sem `key`, sem `value` ou com `key` de mais de 256 caracteres dá 400 e não grava."""
        cases = {
            'no key': {'value': 'v'},
            'no value': {'key': 'k'},
            'key of 257 characters': {'key': 'k' * 257, 'value': 'v'},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                response = self._post(data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(UserSettings.objects.count(), 0, msg='nothing may be stored')

    def test_key_of_exactly_256_characters_is_accepted(self):
        """AC-8930-7 (controle): a chave de exatamente 256 caracteres, o limite do campo, é aceita."""
        response = self._post({'key': 'k' * 256, 'value': 'v'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserSettings.objects.get().key, 'k' * 256)

    def test_key_with_two_rows_updates_all_of_them(self):
        """AC-8930-8: a chave que já tem duas linhas atualiza o valor das duas e responde 200 sem corpo."""
        UserSettings.objects.create(user=self.user, key='dup', value='old')
        UserSettings.objects.create(user=self.user, key='dup', value='old')
        response = self._post({'key': 'dup', 'value': 'new'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        self.assertEqual(list(UserSettings.objects.values_list('value', flat=True)), ['new', 'new'])

    def test_new_key_is_created_and_an_empty_value_is_valid(self):
        """AC-8930-15 (controle): uma chave nova é criada, e o `value` vazio é válido."""
        for key, value in (('fresh', 'v'), ('empty', '')):
            with self.subTest(key=key):
                response = self._post({'key': key, 'value': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'')
                self.assertEqual(UserSettings.objects.get(user=self.user, key=key).value, value)
