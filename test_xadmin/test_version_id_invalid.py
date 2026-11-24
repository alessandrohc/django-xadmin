# coding=utf-8
"""O identificador de versao da rota de reversao e da de recuperacao: 400 se a chave nao o converte. See #9104.

``RevisionView.init_request`` e ``RecoverView.init_request`` passavam o ``version_id`` da rota, cru, ao
``get_object_or_404``: o que a chave da ``Version`` nao converte em numero inteiro (``abc``, ``1.5``, o byte NUL…)
virava ``ValueError`` e a rota respondia 500. Agora a conversao vem na busca da versao, depois dos portoes
(permissao, objeto da rota, modelo sem historico no reversion): o que a chave nao converte dá 400; o que ela
converte e nenhuma versao tem segue dando 404.

Aqui nao ha o ``NullByteRequestMiddleware`` do plus_base (no host o NUL no caminho já é 400 antes da view): o NUL
chega a view, e os casos de portao (403 e 404 do modelo sem historico) ficam sem o NUL porque no host ele e 400.

O cliente e montado com ``raise_request_exception=False`` para um 500 aparecer como status.

    python runtests.py test_xadmin.test_version_id_invalid
    # no container do projeto host:
    docker compose exec -T django bash -lc \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_version_id_invalid'
"""
from urllib.parse import quote

import reversion
from django.test import Client, TestCase
from reversion.models import Version

from test_xadmin.fixtureapp.models import Author, Ranked
from test_xadmin.test_revision_compare_scope import _first_version, _staff

REVISION_TEMPLATE = 'xadmin/views/revision_form.html'
RECOVER_TEMPLATE = 'xadmin/views/recover_form.html'
POST_DATA = {'name': 'posted-name', 'secret': 'posted-secret'}
# o que a chave da `Version` nao converte em numero inteiro (AC-9104-1); o NUL vai na URL como %00
NOT_A_NUMBER = ('abc', '1.5', '1e3', '0x10', ' ', '1\x00', '\x00')
# o que o campo da chave converte e nenhuma versao tem (AC-9104-2). O digito Unicode e escrito como 987654321 e nao
# como `٣` (3): o fixture dos moldes tem a versao 3, de outro modelo, e o 404 viria do escopo (AC-9002-1), nao de
# ninguem a ter. O de 29 digitos esta fora do intervalo do banco.
UNOWNED_NUMBERS = ('987654321', '-1', '٩٨٧٦٥٤٣٢١', ' 987654321', '9' * 29)
# os portoes (AC-9104-3, AC-9104-4, AC-9002-7) sem o NUL: no host ele e 400 antes da view
NOT_A_NUMBER_NO_NUL = ('abc', '1.5')


class VersionIdTests(TestCase):
    """O `Author` (reversion habilitado) e o `Ranked` (sem reversion) do fixture."""

    @classmethod
    def setUpTestData(cls):
        cls.editor = _staff('vid-editor', 'add_author', 'change_author', 'delete_author', 'view_author')
        cls.viewer = _staff('vid-viewer', 'view_author', 'change_author')
        cls.ranked_editor = _staff('vid-ranked', 'add_ranked', 'change_ranked', 'delete_ranked', 'view_ranked')
        with reversion.create_revision():
            cls.live = Author.objects.create(name='live-a', secret='secret-live')
        with reversion.create_revision():
            cls.gone = Author.objects.create(name='gone-a', secret='secret-gone')
        cls.gone_pk = cls.gone.pk
        cls.gone.delete()
        cls.ranked = Ranked.objects.create(name='r1', position=1)
        cls.author_count = Author.objects.count()

    def _client_for(self, user):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        return client

    def _revision_url(self, version_id, model='author', object_pk=None):
        object_pk = self.live.pk if object_pk is None else object_pk
        return f'/xadmin/xadmin_fixture/{model}/{object_pk}/revision/{quote(str(version_id), safe="")}/'

    def _recover_url(self, version_id, model='author'):
        return f'/xadmin/xadmin_fixture/{model}/recover/{quote(str(version_id), safe="")}/'

    def _author_routes(self, version_id):
        """As duas rotas de versao do `Author`: (nome, URL, template do formulario)."""
        return (
            ('revision', self._revision_url(version_id), REVISION_TEMPLATE),
            ('recover', self._recover_url(version_id), RECOVER_TEMPLATE),
        )

    def _listed(self):
        """A versao que a lista de recuperacao do Author oferece para o apagado."""
        offered = list(Version.objects.get_deleted(Author))
        self.assertEqual([v.object_id for v in offered], [str(self.gone_pk)], msg='fixture: one deleted Author listed')
        return offered[0]

    def _assert_author_untouched(self):
        self.live.refresh_from_db()
        self.assertEqual((self.live.name, self.live.secret), ('live-a', 'secret-live'), msg='the Author was saved')
        self.assertEqual(Author.objects.count(), self.author_count, msg='an Author was created')
        self.assertFalse(Author.objects.filter(pk=self.gone_pk).exists(), msg='the deleted Author was recovered')

    def _send(self, user, method, url, data=None):
        client = self._client_for(user)
        return client.get(url) if method == 'get' else client.post(url, data or POST_DATA)

    def test_invalid_version_id_answers_400_without_the_form_and_writes_nothing(self):
        """AC-9104-1: reversão ou recuperação com identificador de versão que a chave não converte dá 400, sem formulário e sem gravar."""
        # precondicao positiva: sem ela o 400 valeria numa rota que nunca abre
        own = _first_version(self.live)
        self.assertIsNotNone(own, msg='fixture: the live Author has a version')
        control_revision = self._send(self.editor, 'get', self._revision_url(own.pk))
        self.assertEqual(control_revision.status_code, 200, msg='control: the own version opens the revert form')
        self.assertIn(REVISION_TEMPLATE, [t.name for t in control_revision.templates])
        control_recover = self._send(self.editor, 'get', self._recover_url(self._listed().pk))
        self.assertEqual(control_recover.status_code, 200, msg='control: the listed version opens the recover form')
        self.assertIn(RECOVER_TEMPLATE, [t.name for t in control_recover.templates])
        for value in NOT_A_NUMBER:
            for route, url, template in self._author_routes(value):
                for method in ('get', 'post'):
                    with self.subTest(route=route, method=method, version_id=value):
                        # os subtestes dividem o banco: um POST que gravou nao pode contaminar o caso seguinte
                        Author.objects.filter(pk=self.live.pk).update(name='live-a', secret='secret-live')
                        response = self._send(self.editor, method, url)
                        self.assertEqual(response.status_code, 400)
                        self.assertNotIn(template, [t.name for t in response.templates],
                                         msg='the form must not be shown')
                        if method == 'post':
                            self._assert_author_untouched()

    def test_unowned_numeric_version_id_answers_404_in_both_routes(self):
        """AC-9104-2 (controle): o identificador que a chave converte e nenhuma versão tem dá 404 na reversão e na recuperação, no GET e no POST."""
        pk_field = Version._meta.pk
        for value in UNOWNED_NUMBERS:
            # o campo converte o valor (a fronteira com o AC-9104-1) e nenhuma versao do banco tem essa chave
            converted = pk_field.to_python(value)
            self.assertFalse(Version.objects.filter(pk=converted).exists(),
                             msg=f'fixture: no version has the key {value!r} converts to')
        for value in UNOWNED_NUMBERS:
            for route, url, __ in self._author_routes(value):
                for method in ('get', 'post'):
                    with self.subTest(route=route, method=method, version_id=value):
                        response = self._send(self.editor, method, url)
                        self.assertEqual(response.status_code, 404)

    def test_model_without_history_keeps_answering_404_to_an_invalid_version_id(self):
        """AC-9104-3 (controle): o modelo sem histórico no reversion segue dando 404 à reversão e à recuperação com identificador que a chave não converte."""
        # precondicao positiva: o usuario alcanca o modelo (um 404 de permissao seria 403)
        control = self._send(self.ranked_editor, 'get', f'/xadmin/xadmin_fixture/ranked/{self.ranked.pk}/update/')
        self.assertEqual(control.status_code, 200, msg='control: the Ranked editor reaches the model')
        for value in NOT_A_NUMBER_NO_NUL:
            routes = (
                ('revision', self._revision_url(value, model='ranked', object_pk=self.ranked.pk)),
                ('recover', self._recover_url(value, model='ranked')),
            )
            for route, url in routes:
                for method in ('get', 'post'):
                    with self.subTest(route=route, method=method, version_id=value):
                        response = self._send(self.ranked_editor, method, url, {'name': 'x', 'position': 2})
                        self.assertEqual(response.status_code, 404)
                        self.ranked.refresh_from_db()
                        self.assertEqual((self.ranked.name, self.ranked.position), ('r1', 1),
                                         msg='the Ranked must not be saved')

    def test_recover_without_permission_answers_403_to_an_invalid_version_id(self):
        """AC-9104-4 (controle): sem a permissão de adicionar, alterar e excluir, a recuperação dá 403 também com identificador que a chave não converte."""
        control = self._send(self.editor, 'get', self._recover_url(self._listed().pk))
        self.assertEqual(control.status_code, 200, msg='control: the editor opens the listed version')
        for value in NOT_A_NUMBER_NO_NUL:
            for method in ('get', 'post'):
                with self.subTest(method=method, version_id=value):
                    response = self._send(self.viewer, method, self._recover_url(value))
                    self.assertEqual(response.status_code, 403)
                    self.assertFalse(Author.objects.filter(pk=self.gone_pk).exists(),
                                     msg='the deleted Author must stay deleted')

    def test_revision_gates_answer_before_an_invalid_version_id(self):
        """AC-9002-7 (controle): na reversão, sem a permissão dá 403 e o objeto da rota inexistente dá 404, também com identificador que a chave não converte."""
        control = self._send(self.editor, 'get', self._revision_url(_first_version(self.live).pk))
        self.assertEqual(control.status_code, 200, msg='control: the editor opens the own version')
        for value in NOT_A_NUMBER_NO_NUL:
            for method in ('get', 'post'):
                with self.subTest(gate='no permission', method=method, version_id=value):
                    response = self._send(self.viewer, method, self._revision_url(value))
                    self.assertEqual(response.status_code, 403)
                with self.subTest(gate='missing object', method=method, version_id=value):
                    response = self._send(self.editor, method, self._revision_url(value, object_pk=987654))
                    self.assertEqual(response.status_code, 404)
