# coding=utf-8
"""The ``save-order/`` route answers 403 to a user of the staff who may not change the model. See #8984.

The route is registered for every model (``sortablelist.py``, end of the file) and its view checked no
permission at all: the site gate only asks for an active staff user, so one without any permission over
the model reordered its rows (the ``blind`` actor below got a 200 and a new order). The route now refuses
that user with a 403 before anything else: before the 404 of a model without the sortable list (#8986) and
before the ids are read (#8930), so the answer does not tell a user without permission whether a model
sorts or whether an id exists.

These tests pin that answer for the four kinds of actor the criteria list (only ``view_``, only ``add_``
and ``delete_``, nothing at all, only the ``change_`` of another model) on the four models of the fixture
(an integer key and a UUID key, each with and without the sortable list), and pin what must not change
around it: the superuser and the user that only has ``change_`` still save (AC-8984-5), an admin that takes
``change`` away refuses even who holds the permission (AC-8984-6), and the site gate still answers before
the view (AC-8930-18).

The client is built with ``raise_request_exception=False`` so a 500 shows up as a status to compare. The
attribute ``client`` of the ``TestCase`` is left alone: the actor's client is built per call.

    python runtests.py test_xadmin.test_save_order_permission
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_save_order_permission'
"""
import json
import uuid
from unittest import mock

from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase

from xadmin.plugins.sortablelist import SaveOrderView
from xadmin.sites import site

from test_xadmin.fixtureapp.models import Author, Ranked, RankedUuid, Tag

AUTHOR_URL = '/xadmin/xadmin_fixture/author/save-order/'
TAG_URL = '/xadmin/xadmin_fixture/tag/save-order/'
RANKED_URL = '/xadmin/xadmin_fixture/ranked/save-order/'
RANKED_UUID_URL = '/xadmin/xadmin_fixture/rankeduuid/save-order/'

# os mesmos valores de test_save_order_route.py (linhas 43-44): o MISSING_ID a chave inteira aceita e nenhum objeto tem;
# o NUL_ID nenhuma chave converte
MISSING_ID = '987654321'
NUL_ID = '1\x00'


def _staff(username, *codenames, superuser=False):
    """Staff user (xadmin only lets staff in) with exactly the given permissions."""
    user = User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password',
                                    is_staff=True, is_superuser=superuser)
    if codenames:
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def _table(model):
    """The whole table of the model, to compare before and after a request."""
    return list(model.objects.order_by('pk').values())


def _positions(model):
    return dict(model.objects.values_list('pk', 'position'))


def _inputs(first, second, missing):
    """The ``order[]`` values the criteria enumerate, as ``{caso: ordem}``; ``None`` sends no ``order[]`` at all."""
    return {
        'no order[]': None,
        'ids of two objects in another order': [second, first],
        'text that is not a key': ['abc'],
        'empty text': [''],
        'NUL byte': [NUL_ID],
        'id of no object': [missing],
        'id of an object and one of no object': [first, missing],
        'text that is not a key next to the id of an object': [first, 'abc'],
    }


class _ClientMixin:
    """A client that turns an unhandled exception into a 500 response instead of raising."""

    def _client_for(self, user=None, **extra):
        client = Client(raise_request_exception=False, **extra)
        if user is not None:
            client.force_login(user)
        return client

    def _post(self, user, url, order=None):
        """POST the route as ``user``; ``order=None`` sends no ``order[]`` at all."""
        data = {} if order is None else {'order[]': [str(item) for item in order]}
        return self._client_for(user).post(url, data)


class _RouteTestCase(_ClientMixin, TestCase):
    """The actors, the models and the rows every test of the module uses."""

    @classmethod
    def setUpTestData(cls):
        views = ('view_ranked', 'view_rankeduuid', 'view_author', 'view_tag')
        changes = ('change_ranked', 'change_rankeduuid', 'change_author', 'change_tag')
        # quem pode alterar: o `changer` tem também a de ver; o `change_only` só a de alterar (o xadmin deriva a de ver da de
        # alterar, base.py:742-744); o superusuário tem toda permissão
        cls.changer = _staff('perm-changer', *views, *changes)
        cls.change_only = _staff('perm-change-only', *changes)
        cls.superuser = _staff('perm-superuser', superuser=True)
        # quem não pode alterar: os quatro atores do AC-8984-1 e do AC-8984-2
        cls.viewer = _staff('perm-viewer', *views)
        cls.add_delete = _staff('perm-add-delete', 'add_ranked', 'delete_ranked', 'add_rankeduuid', 'delete_rankeduuid',
                                'add_author', 'delete_author', 'add_tag', 'delete_tag')
        cls.blind = _staff('perm-blind')
        # a permissão de OUTRO modelo, por rota: com a da própria rota o ator cairia no 404 (modelo sem ordenação) ou no
        # 200 (modelo com ordenação), e não no 403
        cls.other_author = _staff('perm-other-author', 'view_author', 'change_author')
        cls.other_ranked = _staff('perm-other-ranked', 'view_ranked', 'change_ranked')
        # fora da equipe, com as permissões do `Ranked`: o portão do site responde antes da view
        cls.plain_with_perms = User.objects.create_user('perm-plain', 'perm-plain@example.com', 'not-a-real-password')
        cls.plain_with_perms.user_permissions.set(Permission.objects.filter(codename__in=['view_ranked', 'change_ranked']))
        cls.ranked = [Ranked.objects.create(name=f'r{i}', position=10 * i) for i in (1, 2, 3)]
        cls.ranked_uuid = [RankedUuid.objects.create(name=f'u{i}', position=10 * i) for i in (1, 2, 3)]
        cls.authors = [Author.objects.create(name=f'a{i}') for i in (1, 2)]
        cls.tags = [Tag.objects.create(name=f't{i}') for i in (1, 2)]

    def _route(self, name):
        """``(rota, modelo, pks dos objetos, um id sem objeto)`` de um dos quatro modelos do fixture."""
        return {
            'ranked': (RANKED_URL, Ranked, [obj.pk for obj in self.ranked], MISSING_ID),
            'rankeduuid': (RANKED_UUID_URL, RankedUuid, [obj.pk for obj in self.ranked_uuid], str(uuid.uuid4())),
            'author': (AUTHOR_URL, Author, [obj.pk for obj in self.authors], MISSING_ID),
            'tag': (TAG_URL, Tag, [obj.pk for obj in self.tags], str(uuid.uuid4())),
        }[name]

    def _denied(self, other_model_actor):
        """Os quatro atores sem a permissão de alterar, na ordem do critério; o último é o da permissão de outro modelo."""
        return {
            'only view': self.viewer,
            'only add and delete': self.add_delete,
            'no permission': self.blind,
            'only the change of another model': other_model_actor,
        }

    def _reset(self, model):
        """Volta as posições do modelo ordenável ao estado de partida (10, 20, 30), para o teste ver só o efeito da chamada."""
        for rank, pk in enumerate(model.objects.order_by('name').values_list('pk', flat=True), start=1):
            model.objects.filter(pk=pk).update(position=10 * rank)


class SortableModelPermissionTests(_RouteTestCase):
    """AC-8984-1: o modelo com a ordenação habilitada recusa com 403 quem não pode alterar."""

    def test_actor_without_the_permission_to_change_gets_403_and_changes_no_order(self):
        """AC-8984-1: sem a permissão de alterar o modelo, qualquer `order[]` dá 403 e a ordem de ninguém muda."""
        for name in ('ranked', 'rankeduuid'):
            url, model, pks, missing = self._route(name)
            # controle positivo: quem pode alterar grava pela mesma rota, então o 403 abaixo é da permissão e não do ambiente
            self._reset(model)
            control = self._post(self.changer, url, [pks[2], pks[0], pks[1]])
            self.assertEqual(control.status_code, 200, msg='control: the actor who may change must save the order')
            self.assertEqual(_positions(model), {pks[2]: 1, pks[0]: 2, pks[1]: 3})
            self._reset(model)

            for who, actor in self._denied(self.other_author).items():
                for case, order in _inputs(pks[0], pks[1], missing).items():
                    with self.subTest(model=name, actor=who, case=case):
                        before = _table(model)
                        self.assertTrue(before, msg='the table must have rows for the comparison to mean something')
                        response = self._post(actor, url, order)
                        self.assertEqual(response.status_code, 403,
                                         msg='a user who may not change the model must be refused with a 403')
                        self.assertEqual(_table(model), before, msg='no object of the model may change')


class UnsortableModelPermissionTests(_RouteTestCase):
    """AC-8984-2: o modelo sem ordenação também recusa com 403 quem não pode alterar, antes do 404 do #8986."""

    def test_actor_without_the_permission_to_change_gets_403_on_a_model_without_the_sortable_list(self):
        """AC-8984-2: o modelo sem lista ordenável dá 403 ao sem permissão, o mesmo status do modelo que ordena, sem mudar objeto."""
        # (modelo sem ordenação, o modelo com ordenação de mesma chave que serve de comparação, o ator de outro modelo de cada
        # rota): o `other-model` da rota do `Author` e do `Tag` tem o `change_` do `Ranked`, e o das rotas que ordenam o do `Author`
        pairs = (('author', 'ranked', self.other_ranked, self.other_author),
                 ('tag', 'rankeduuid', self.other_ranked, self.other_author))
        for name, sortable, other_here, other_there in pairs:
            url, model, pks, missing = self._route(name)
            sort_url, _sort_model, sort_pks, sort_missing = self._route(sortable)
            here = _inputs(pks[0], pks[1], missing)
            there = _inputs(sort_pks[0], sort_pks[1], sort_missing)
            # controle positivo: quem pode alterar recebe o 404 do #8986 (o AC-8986-1 vivo) com a mesma entrada, então a
            # diferença de resposta entre os dois lados é só a permissão
            for case, order in here.items():
                with self.subTest(model=name, actor='who may change', case=case):
                    control = self._post(self.changer, url, order)
                    self.assertEqual(control.status_code, 404,
                                     msg='control: who may change gets the 404 of a model without the sortable list')

            denied_here = self._denied(other_here)
            denied_there = self._denied(other_there)
            for who in denied_here:
                for case, order in here.items():
                    with self.subTest(model=name, actor=who, case=case):
                        before = _table(model)
                        self.assertTrue(before, msg='the table must have rows for the comparison to mean something')
                        response = self._post(denied_here[who], url, order)
                        self.assertEqual(response.status_code, 403,
                                         msg='a user who may not change the model must get a 403, not the 404 of the model')
                        self.assertEqual(_table(model), before, msg='no object of the model may change')
                        # o mesmo ator, a mesma entrada no modelo que ordena: o mesmo status (a razão da D3: a resposta não
                        # diz se o modelo ordena); a rota que ordena grava no modelo dela, que o teste repõe em seguida
                        twin = self._post(denied_there[who], sort_url, there[case])
                        self.assertEqual(response.status_code, twin.status_code,
                                         msg='the answer must not tell whether the model has the sortable list')

    def test_admin_with_an_empty_sortable_field_also_gets_403_for_who_may_not_change(self):
        """AC-8984-2: o admin com `list_order_field` vazio (`None` ou texto vazio) dá 403 ao sem permissão, 404 a quem pode."""
        # o patch é no admin que já declara o campo (`Ranked`), como em test_save_order_route.py (o conjunto de nomes do admin
        # fica no cache do site, `_admin_plugins_cache`, sites.py:431-435): um nome criado só para o teste ficaria nele
        url, model, pks, _missing = self._route('ranked')
        self._reset(model)
        control = self._post(self.changer, url, [pks[2], pks[0], pks[1]])
        self.assertEqual(control.status_code, 200, msg='control: the declared field must let the route save')
        self._reset(model)
        for empty in (None, ''):
            with mock.patch.object(site._registry[Ranked], 'list_order_field', empty):
                gate = self._post(self.changer, url, [pks[0]])
                self.assertEqual(gate.status_code, 404, msg='control: who may change gets the 404 of a model that does not sort')
                for who, actor in self._denied(self.other_author).items():
                    with self.subTest(list_order_field=empty, actor=who):
                        before = _table(model)
                        response = self._post(actor, url, [pks[2], pks[0], pks[1]])
                        self.assertEqual(response.status_code, 403,
                                         msg='an empty list_order_field must not turn the 403 into the 404')
                        self.assertEqual(_table(model), before, msg='no object of the model may change')


class WhoMayChangeTests(_RouteTestCase):
    """AC-8984-5 e AC-8984-6: quem pode alterar segue gravando; o admin que retira a permissão recusa a todos."""

    def test_actor_with_only_the_change_permission_and_the_superuser_still_save_the_order(self):
        """AC-8984-5: o ator só com `change_` (sem `view_`) e o superusuário gravam a ordem e recebem 200 com um JSON vazio."""
        for name in ('ranked', 'rankeduuid'):
            url, model, pks, _missing = self._route(name)
            for who, actor in (('only change', self.change_only), ('superuser', self.superuser)):
                with self.subTest(model=name, actor=who):
                    self._reset(model)
                    response = self._post(actor, url, [pks[2], pks[0], pks[1]])
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(json.loads(response.content), {})
                    self.assertEqual(_positions(model), {pks[2]: 1, pks[0]: 2, pks[1]: 3},
                                     msg='the order must be saved as sent')

    def test_admin_that_removes_the_change_permission_refuses_who_holds_it(self):
        """AC-8984-6: o admin com `remove_permissions` contendo `'change'` dá 403 ao que tem a permissão e ao superusuário."""
        url, model, pks, _missing = self._route('ranked')
        actors = (('who may change', self.changer), ('superuser', self.superuser))
        # controle positivo, antes do patch: os dois gravam; sem isso o 403 abaixo podia ser do ambiente
        for who, actor in actors:
            with self.subTest(control=who):
                self._reset(model)
                saved = self._post(actor, url, [pks[2], pks[0], pks[1]])
                self.assertEqual(saved.status_code, 200, msg='control: without the patch the actor must save')
                self.assertEqual(_positions(model), {pks[2]: 1, pks[0]: 2, pks[1]: 3})

        # o `remove_permissions` do admin chega à view por herança (o admin é classe-base da view montada, sites.py:519-546):
        # o patch é no admin registrado, e o `patch` o desfaz ao sair (o nome só vai ao `_admin_plugins_cache`, inerte
        # porque nenhum plugin declara `remove_permissions`)
        with mock.patch.object(site._registry[Ranked], 'remove_permissions', ['change'], create=True):
            merged = site.get_view_class(SaveOrderView, site._registry[Ranked])
            self.assertEqual(merged.remove_permissions, ['change'],
                             msg='the admin attribute must reach the view the route builds')
            for who, actor in actors:
                with self.subTest(patched=who):
                    self._reset(model)
                    before = _table(model)
                    response = self._post(actor, url, [pks[2], pks[0], pks[1]])
                    self.assertEqual(response.status_code, 403,
                                     msg='an admin that removes the change permission refuses everybody')
                    self.assertEqual(_table(model), before, msg='no order may change')

        # depois do `patch` a rota volta a gravar: o patch não vazou para a view nem para o cache do site
        self._reset(model)
        again = self._post(self.changer, url, [pks[2], pks[0], pks[1]])
        self.assertEqual(again.status_code, 200, msg='the patch must not leak out of the test')


class SiteGateTests(_RouteTestCase):
    """AC-8930-18: o portão do site responde antes da view, então a permissão da rota não o alcança."""

    def test_visitor_and_non_staff_user_with_the_model_permissions_stay_at_the_site_gate(self):
        """AC-8930-18 (controle): o visitante e o usuário fora da equipe, com as permissões do modelo, seguem no portão do site."""
        clients = {'visitor': self._client_for(), 'non-staff with the permissions': self._client_for(self.plain_with_perms)}
        ranked_url, _ranked_model, ranked_pks, _missing = self._route('ranked')
        author_url, _author_model, author_pks, _missing = self._route('author')
        ranked_before, author_before = _table(Ranked), _table(Author)
        for who, client in clients.items():
            for model, url, valid in (('ranked', ranked_url, [ranked_pks[0]]), ('author', author_url, [author_pks[0]])):
                with self.subTest(who=who, model=model):
                    gate = client.post(url, {'order[]': valid})
                    response = client.post(url, {'order[]': ['abc']})
                    self.assertEqual(response.status_code, gate.status_code,
                                     msg='the id the key does not convert must get the answer the gate gives')
                    self.assertEqual(response.status_code, 200, msg='the gate is the login page')
                    self.assertFalse(response.has_header('Location'), msg='the gate does not redirect')
                    self.assertNotIn(gate.status_code, (400, 403, 404, 500), msg='the gate answers before the route')
                    self.assertEqual(_table(Ranked), ranked_before)
                    self.assertEqual(_table(Author), author_before)


class MethodTests(_RouteTestCase):
    """Robustez (sem critério): a recusa sai no `setup`, então vale para todo método HTTP."""

    def test_get_gets_403_for_who_may_not_change_and_405_for_who_may(self):
        """Robustez (sem critério): o `GET` da rota dá 403 a quem não pode alterar e segue 405 a quem pode."""
        # a view só tem `post`: o 405 é o de hoje para todos; o guarda roda antes do método (`BaseAdminView.setup`,
        # base.py:395-406), então o sem permissão passa a receber 403 também no `GET`
        for who, actor in self._denied(self.other_author).items():
            with self.subTest(actor=who):
                response = self._client_for(actor).get(RANKED_URL)
                self.assertEqual(response.status_code, 403, msg='the guard runs before the HTTP method')
        response = self._client_for(self.changer).get(RANKED_URL)
        self.assertEqual(response.status_code, 405, msg='control: who may change still gets the 405 of a route with only post')
