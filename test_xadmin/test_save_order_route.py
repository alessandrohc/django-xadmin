# coding=utf-8
"""The ``save-order/`` route answers 404 to a model whose admin does not turn the sortable list on. See #8986.

The route is registered for every model (``sortablelist.py``, end of the file), but only the
admin that declares ``list_order_field`` has a sortable list. For any other model a forged
POST got a 500 (``AttributeError`` from ``save_order``) when the ids were of existing
objects, a 400 or a 404 from the id checks of #8930, or a 200 "success" without ``order[]``,
and none of them touched a row. These tests pin the answer the route gives now: 404, before
the ids are read, and no row of the model changes.

The same module pins what must not change around the gate: the sortable list of a UUID key
(saves a valid order, 400 for text that is not a UUID, 404 for a UUID no object owns), the
CSRF check of the route (403 without a valid token) and the site gate (a visitor and a user
outside the staff get the login page, never the 404 of the route).

The client is built with ``raise_request_exception=False`` so a 500 shows up as a status to
compare, and the red fails on the assertion instead of erroring on the exception. The
attribute ``client`` of the ``TestCase`` is left alone: the actor's client is built per call.

    python runtests.py test_xadmin.test_save_order_route
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_save_order_route'
"""
import json
import uuid
from unittest import mock

from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase

from xadmin.sites import site

from test_xadmin.fixtureapp.models import Author, Ranked, RankedUuid, Tag

AUTHOR_URL = '/xadmin/xadmin_fixture/author/save-order/'
TAG_URL = '/xadmin/xadmin_fixture/tag/save-order/'
RANKED_URL = '/xadmin/xadmin_fixture/ranked/save-order/'
RANKED_UUID_URL = '/xadmin/xadmin_fixture/rankeduuid/save-order/'

# os mesmos valores de test_invalid_post_input.py (linhas 37 e 40): o MISSING_ID a chave inteira aceita e nenhum objeto
# tem; o NUL_ID nenhuma chave converte
MISSING_ID = '987654321'
NUL_ID = '1\x00'


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


def _positions(model):
    return dict(model.objects.values_list('pk', 'position'))


class _ClientMixin:
    """A client that turns an unhandled exception into a 500 response instead of raising."""

    def _client_for(self, user=None, **extra):
        client = Client(raise_request_exception=False, **extra)
        if user is not None:
            client.force_login(user)
        return client


class UnsortableModelTests(_ClientMixin, TestCase):
    """The ``save-order/`` route of the models whose admin does not declare ``list_order_field``."""

    @classmethod
    def setUpTestData(cls):
        # o ator tem a permissão de alterar os modelos: é o que o #8984 vai exigir da rota, e o 404 vale para ele
        cls.changer = _staff('order-gate-changer', 'view_author', 'change_author', 'view_tag', 'change_tag',
                             'view_ranked', 'change_ranked')
        cls.a1 = Author.objects.create(name='a1')
        cls.a2 = Author.objects.create(name='a2')
        cls.t1 = Tag.objects.create(name='t1')
        cls.t2 = Tag.objects.create(name='t2')
        cls.r1 = Ranked.objects.create(name='r1', position=10)
        cls.r2 = Ranked.objects.create(name='r2', position=20)
        cls.r3 = Ranked.objects.create(name='r3', position=30)

    def _post(self, url, order=None):
        """POST the route; ``order=None`` sends no ``order[]`` at all."""
        data = {} if order is None else {'order[]': list(order)}
        return self._client_for(self.changer).post(url, data)

    def test_model_without_the_sortable_list_answers_404_and_changes_nothing(self):
        """AC-8986-1: o modelo cujo admin não habilita a ordenação dá 404, com qualquer `order[]`, sem mudar objeto."""
        # controle: o modelo que ordena, pelo mesmo ator e pela mesma rota, grava; sem isso o 404 abaixo podia ser do ambiente
        control = self._post(RANKED_URL, [self.r3.pk, self.r1.pk, self.r2.pk])
        self.assertEqual(control.status_code, 200, msg='control: the sortable model must save its order')
        self.assertEqual(_positions(Ranked), {self.r3.pk: 1, self.r1.pk: 2, self.r2.pk: 3})

        models = (
            # (nome, rota, modelo, ids de objetos, id sem objeto): a chave inteira e a chave UUID
            ('author', AUTHOR_URL, Author, [self.a1.pk, self.a2.pk], MISSING_ID),
            ('tag', TAG_URL, Tag, [str(self.t1.pk), str(self.t2.pk)], str(uuid.uuid4())),
        )
        for name, url, model, (first, second), missing in models:
            self.assertTrue(_table(model), msg='the table must have rows for the comparison to mean something')
            cases = {
                'no order[]': None,
                'empty id': [''],
                'text that is not a key': ['abc'],
                'NUL byte': [NUL_ID],
                'id of one object': [first],
                'ids of two objects': [first, second],
                'id of no object': [missing],
                'id of an object and one of no object': [first, missing],
                'text that is not a key next to the id of an object': [first, 'abc'],
            }
            for case, order in cases.items():
                with self.subTest(model=name, case=case):
                    before = _table(model)
                    response = self._post(url, order)
                    self.assertEqual(response.status_code, 404,
                                     msg='a model without the sortable list must answer 404 whatever order[] says')
                    self.assertEqual(_table(model), before, msg='no object of the model may change')

    def test_admin_with_an_empty_sortable_field_answers_404_like_one_without_it(self):
        """AC-8986-1: o admin com `list_order_field` vazio (`None` ou texto vazio) dá 404, como o que não o declara."""
        # o `SortableListPlugin` trata os dois valores como lista desligada (sortablelist.py:28). O patch é no admin que já
        # declara o campo (`Ranked`), e não num atributo criado: o conjunto de nomes do admin fica no cache do site
        # (`_admin_plugins_cache`, sites.py:431-435, preenchido na 1ª mesclagem do admin e só limpo por `get_urls()`,
        # sites.py:677) e um nome criado só para o teste ficaria nele depois do teste
        control = self._post(RANKED_URL, [self.r3.pk, self.r1.pk, self.r2.pk])
        self.assertEqual(control.status_code, 200, msg='control: the declared field must let the route save')
        for empty in (None, ''):
            with self.subTest(list_order_field=empty):
                before = _table(Ranked)
                with mock.patch.object(site._registry[Ranked], 'list_order_field', empty):
                    response = self._post(RANKED_URL, [self.r1.pk])
                self.assertEqual(response.status_code, 404, msg='an empty list_order_field must answer 404')
                self.assertEqual(_table(Ranked), before, msg='no object of the model may change')


class UuidKeyOrderTests(_ClientMixin, TestCase):
    """The sortable list of a model whose key is a UUID (``RankedUuid``): what the route already did, now pinned."""

    @classmethod
    def setUpTestData(cls):
        cls.changer = _staff('order-uuid-changer', 'view_rankeduuid', 'change_rankeduuid')
        cls.u1 = RankedUuid.objects.create(name='u1', position=10)
        cls.u2 = RankedUuid.objects.create(name='u2', position=20)
        cls.u3 = RankedUuid.objects.create(name='u3', position=30)

    def _post(self, order):
        return self._client_for(self.changer).post(RANKED_UUID_URL, {'order[]': [str(pk) for pk in order]})

    def _ids(self):
        return [self.u1.pk, self.u2.pk, self.u3.pk]

    def test_valid_uuid_order_is_saved_and_answers_an_empty_json_object(self):
        """AC-8986-2: a ordenação de chave UUID com ids de objetos grava a nova ordem e responde 200 com um JSON vazio."""
        response = self._post([self.u3.pk, self.u1.pk, self.u2.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {})
        self.assertEqual(_positions(RankedUuid), {self.u3.pk: 1, self.u1.pk: 2, self.u2.pk: 3})

    def test_item_that_is_not_a_uuid_answers_400_and_changes_no_order(self):
        """AC-8986-3: ao menos um item que a chave UUID não converte dá 400 e a ordem de ninguém muda."""
        before = _positions(RankedUuid)
        for bad in ('abc', '', NUL_ID):
            # o válido que vem antes do inválido não pode ter sido gravado: o 400 é um `return` e não desfaz o `atomic`
            for order in ([bad], [self.u1.pk, bad], [bad, self.u1.pk]):
                with self.subTest(bad=bad, order=order):
                    response = self._post(order)
                    self.assertEqual(response.status_code, 400, msg='text that is not a UUID must answer 400')
                    self.assertEqual(_positions(RankedUuid), before, msg='no order may change')

    def test_uuid_no_object_owns_answers_404_and_changes_no_order(self):
        """AC-8986-4: todos os itens convertidos e algum UUID sem objeto dá 404 e a ordem de ninguém muda."""
        before = _positions(RankedUuid)
        missing = uuid.uuid4()
        for order in ([missing], [self.u1.pk, missing], [missing, self.u1.pk], self._ids() + [missing]):
            with self.subTest(order=[str(pk) for pk in order]):
                response = self._post(order)
                self.assertEqual(response.status_code, 404, msg='a UUID no object owns must answer 404')
                self.assertEqual(_positions(RankedUuid), before, msg='no order may change')


class CsrfTests(_ClientMixin, TestCase):
    """The CSRF check of the route: the site stack answers 403 before the view, sortable list or not."""

    @classmethod
    def setUpTestData(cls):
        cls.changer = _staff('order-csrf-changer', 'view_ranked', 'change_ranked', 'view_author', 'change_author')
        cls.r1 = Ranked.objects.create(name='r1', position=10)
        cls.r2 = Ranked.objects.create(name='r2', position=20)
        cls.r3 = Ranked.objects.create(name='r3', position=30)
        cls.author = Author.objects.create(name='a')

    def _csrf_client(self):
        """A logged-in client that, unlike the default one, enforces the CSRF check."""
        return self._client_for(self.changer, enforce_csrf_checks=True)

    def test_post_without_a_valid_csrf_token_answers_403_and_changes_no_order(self):
        """AC-8986-5: sem o token CSRF válido a ordenação dá 403 e a ordem de ninguém muda, com ou sem lista ordenável."""
        # controle positivo: com o token a mesma rota grava, então o 403 abaixo é do token e não do ambiente. A lista
        # entrega o cookie `csrftoken` (o token vai no cabeçalho `X-CSRFToken`, como o JavaScript do xadmin faz)
        with_cookie = self._csrf_client()
        listing = with_cookie.get('/xadmin/xadmin_fixture/ranked/')
        self.assertEqual(listing.status_code, 200, msg='control: the list must load for the actor')
        token = with_cookie.cookies['csrftoken'].value
        saved = with_cookie.post(RANKED_URL, {'order[]': [self.r3.pk, self.r1.pk, self.r2.pk]}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(saved.status_code, 200, msg='control: with a valid token the route must save')
        self.assertEqual(_positions(Ranked), {self.r3.pk: 1, self.r1.pk: 2, self.r2.pk: 3})

        ranked_before, author_before = _positions(Ranked), _table(Author)
        # (cliente, cabeçalho extra): sem cookie nem token; com o cookie mas sem o token (o navegador que o atacante usa
        # envia o cookie e não sabe o token); com o cookie e um token bem formado que não é o dele
        forged = {
            'no cookie and no token': (self._csrf_client(), {}),
            'cookie but no token': (with_cookie, {}),
            'cookie and a token that is not its own': (with_cookie, {'HTTP_X_CSRFTOKEN': 'a' * 64}),
        }
        for how, (client, extra) in forged.items():
            for model, url, order in (('ranked', RANKED_URL, [self.r1.pk, self.r2.pk, self.r3.pk]),
                                      ('author', AUTHOR_URL, [self.author.pk])):
                with self.subTest(how=how, model=model):
                    response = client.post(url, {'order[]': order}, **extra)
                    self.assertEqual(response.status_code, 403, msg='a forged POST must be refused by the CSRF check')
                    self.assertEqual(_positions(Ranked), ranked_before, msg='no order may change')
                    self.assertEqual(_table(Author), author_before, msg='no object may change')


class SiteGateTests(_ClientMixin, TestCase):
    """The gate of the site answers before the route: the 404 of #8986 is not reachable by who is not in."""

    @classmethod
    def setUpTestData(cls):
        cls.plain = User.objects.create_user('order-gate-plain', 'order-gate-plain@example.com', 'not-a-real-password')
        cls.author = Author.objects.create(name='a')
        cls.r1 = Ranked.objects.create(name='r1', position=10)

    def test_visitor_and_non_staff_user_get_the_same_answer_on_a_model_without_the_sortable_list(self):
        """AC-8930-18 (controle): no modelo sem lista ordenável o visitante e o usuário fora da equipe seguem no portão do site."""
        clients = {'visitor': self._client_for(), 'non-staff': self._client_for(self.plain)}
        author_before, ranked_before = _table(Author), _table(Ranked)
        for who, client in clients.items():
            answers = {}
            for model, url, valid in (('author', AUTHOR_URL, [self.author.pk]), ('ranked', RANKED_URL, [self.r1.pk])):
                with self.subTest(who=who, model=model):
                    gate = client.post(url, {'order[]': valid})
                    response = client.post(url, {'order[]': ['abc']})
                    answers[model] = response.status_code
                    self.assertEqual(response.status_code, gate.status_code,
                                     msg='the id the key does not convert must get the answer the gate gives')
                    self.assertEqual(response.status_code, 200, msg='the gate is the login page')
                    self.assertFalse(response.has_header('Location'), msg='the gate does not redirect')
                    self.assertEqual(_table(Author), author_before)
                    self.assertEqual(_table(Ranked), ranked_before)
            # a rota que não ordena e a que ordena respondem o mesmo: o portão não revela qual delas ordena
            self.assertEqual(answers['author'], answers['ranked'], msg=f'{who}: both routes must answer alike')
            self.assertNotIn(404, answers.values(), msg='the 404 of the route is behind the site gate')
