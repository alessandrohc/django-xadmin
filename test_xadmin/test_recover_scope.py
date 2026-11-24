# coding=utf-8
"""A recuperacao de um objeto so aceita a versao que a lista de recuperacao oferece. See #9001.

``RecoverView.init_request`` buscava a versao por ``get_object_or_404(Version, pk=…)``, sem o modelo
da rota nem objeto apagado: quem tinha adicionar/alterar/excluir num modelo lia a versao de qualquer
objeto e de outro modelo (o ``title`` de um ``Book`` na rota do ``Author``), e o POST regravava um
objeto que existe. Agora a busca e ``Version.objects.get_deleted(self.model)``, o mesmo conjunto da
``RecoverListView``: a versao de objeto vivo, de outro modelo ou antiga de um apagado dá 404; o modelo
sem historico no reversion (``Ranked``) levanta ``RegistrationError`` e tambem dá 404.

O cliente e montado com ``raise_request_exception=False`` para um 500 aparecer como status.

    python runtests.py test_xadmin.test_recover_scope
    # no container do projeto host:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_recover_scope'
"""
import reversion
from django.contrib.auth.models import User
from django.test import Client, TestCase
from reversion.models import Version

from test_xadmin.fixtureapp.models import Author, Book, Ranked
from test_xadmin.test_revision_compare_scope import _first_version, _staff

FORM_TEMPLATE = 'xadmin/views/recover_form.html'
# texto que so as versoes que nao sao a oferecida tem: nao pode aparecer nas respostas de 404
FOREIGN_MARKERS = ('live-a', 'secret-live', 'other-b', 'sb-top-secret', 'book-of-b', 'book-gone', 'gone-a-old')
GONE_MARKERS = ('gone-a-final', 'secret-gone')
POST_DATA = {'name': 'posted-name', 'secret': 'posted-secret'}


class RecoverScopeTests(TestCase):
    """Author (reversion habilitado; Book entra como inline dele) e Ranked (sem reversion)."""

    @classmethod
    def setUpTestData(cls):
        cls.editor = _staff('rec-editor', 'add_author', 'change_author', 'delete_author', 'view_author')
        cls.viewer = _staff('rec-viewer', 'view_author', 'change_author')
        cls.ranked_editor = _staff('rec-ranked', 'add_ranked', 'change_ranked', 'delete_ranked', 'view_ranked')
        cls.nonstaff = User.objects.create_user('rec-nonstaff', 'rec-nonstaff@example.com', 'not-a-real-password')
        with reversion.create_revision():
            cls.live = Author.objects.create(name='live-a', secret='secret-live')
        with reversion.create_revision():
            cls.other = Author.objects.create(name='other-b', secret='sb-top-secret')
        with reversion.create_revision():
            cls.book_live = Book.objects.create(author=cls.other, title='book-of-b')
        with reversion.create_revision():
            cls.book_gone = Book.objects.create(author=cls.other, title='book-gone')
        with reversion.create_revision():
            cls.gone = Author.objects.create(name='gone-a-old', secret='secret-gone')
        with reversion.create_revision():
            cls.gone.name = 'gone-a-final'
            cls.gone.save()
        cls.gone_pk = cls.gone.pk
        cls.book_gone_version = _first_version(cls.book_gone)
        cls.gone_versions = list(Version.objects.get_for_object(cls.gone).order_by('pk'))
        cls.gone.delete()
        cls.book_gone.delete()
        cls.ranked = Ranked.objects.create(name='r1', position=1)
        cls.author_count = Author.objects.count()
        cls.book_count = Book.objects.count()

    def _client_for(self, user=None):
        client = Client(raise_request_exception=False)
        if user is not None:
            client.force_login(user)
        return client

    def _url(self, version_pk, model='author'):
        return f'/xadmin/xadmin_fixture/{model}/recover/{version_pk}/'

    def _listed(self):
        """A versao que a lista de recuperacao do Author oferece para o apagado."""
        offered = list(Version.objects.get_deleted(Author))
        self.assertEqual([v.object_id for v in offered], [str(self.gone_pk)], msg='fixture: one deleted Author listed')
        return offered[0]

    def _stranger_versions(self):
        """Versoes que a recuperacao do Author nao deve aceitar (objeto vivo, outro modelo, modelo apagado)."""
        strangers = {
            'a live Author': _first_version(self.live),
            'another live Author': _first_version(self.other),
            'a live Book': _first_version(self.book_live),
            'a deleted Book (another model)': self.book_gone_version,
        }
        for label, version in strangers.items():
            self.assertIsNotNone(version, msg=f'fixture: {label} has a version')
        return strangers

    def _assert_nothing_written(self):
        self.live.refresh_from_db()
        self.assertEqual((self.live.name, self.live.secret), ('live-a', 'secret-live'), msg='a live object was saved')
        self.assertEqual(Author.objects.count(), self.author_count, msg='an Author was created')
        self.assertEqual(Book.objects.count(), self.book_count, msg='a Book was created')

    def _assert_no_foreign_data(self, response, markers=FOREIGN_MARKERS):
        body = response.content.decode('utf-8')
        for marker in markers:
            self.assertTrue(marker not in body, msg=f'the data of the version must not be shown ({marker})')

    def test_stranger_versions_open_as_404_and_show_nothing(self):
        """AC-9001-1: abrir a recuperacao com versao de objeto vivo ou de outro modelo (inclusive sem historico) dá 404."""
        control = self._client_for(self.editor).get(self._url(self._listed().pk))
        self.assertEqual(control.status_code, 200, msg='control: the listed version opens')
        self.assertIn(FORM_TEMPLATE, [t.name for t in control.templates], msg='control: the recover form is used')
        for label, version in self._stranger_versions().items():
            with self.subTest(version=label):
                response = self._client_for(self.editor).get(self._url(version.pk))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(FORM_TEMPLATE, [t.name for t in response.templates])
                self._assert_no_foreign_data(response)
        with self.subTest(version='an Author version on a model without history (Ranked)'):
            response = self._client_for(self.ranked_editor).get(self._url(self._listed().pk, 'ranked'))
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(FORM_TEMPLATE, [t.name for t in response.templates])
            self._assert_no_foreign_data(response, FOREIGN_MARKERS + GONE_MARKERS)

    def test_stranger_versions_post_as_404_and_write_nothing(self):
        """AC-9001-2: enviar a recuperacao com versao de objeto vivo ou de outro modelo (inclusive sem historico) dá 404 e não grava."""
        for label, version in self._stranger_versions().items():
            with self.subTest(version=label):
                # os subtestes dividem o banco: um POST que gravou nao pode contaminar o caso seguinte
                Author.objects.filter(pk=self.live.pk).update(name='live-a', secret='secret-live')
                response = self._client_for(self.editor).post(self._url(version.pk), POST_DATA)
                self.assertEqual(response.status_code, 404)
                self._assert_nothing_written()
        with self.subTest(version='an Author version on a model without history (Ranked)'):
            response = self._client_for(self.ranked_editor).post(
                self._url(self._listed().pk, 'ranked'), {'name': 'x', 'position': 2})
            self.assertEqual(response.status_code, 404)
            self.ranked.refresh_from_db()
            self.assertEqual((self.ranked.name, self.ranked.position), ('r1', 1), msg='the Ranked must not be saved')
            self.assertFalse(Author.objects.filter(pk=self.gone_pk).exists(), msg='the deleted Author must stay deleted')

    def test_listed_version_opens_the_recover_form_with_its_data(self):
        """AC-9001-3: abrir a recuperacao com a versao que a lista oferece mostra o formulario com os dados."""
        response = self._client_for(self.editor).get(self._url(self._listed().pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn(FORM_TEMPLATE, [t.name for t in response.templates])
        body = response.content.decode('utf-8')
        for marker in GONE_MARKERS:
            self.assertTrue(marker in body, msg=f'the recover form must show the data of the version ({marker})')

    def test_listed_version_post_recreates_the_object_and_redirects_to_the_change_screen(self):
        """AC-9001-4: enviar a recuperacao da versao que a lista oferece recria o objeto e redireciona para alterar."""
        self.assertFalse(Author.objects.filter(pk=self.gone_pk).exists(), msg='fixture: the Author is deleted')
        response = self._client_for(self.editor).post(
            self._url(self._listed().pk), {'name': 'recovered-name', 'secret': 'recovered-secret'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/xadmin/xadmin_fixture/author/{self.gone_pk}/update/')
        recovered = Author.objects.get(pk=self.gone_pk)
        self.assertEqual((recovered.name, recovered.secret), ('recovered-name', 'recovered-secret'))

    def test_old_version_of_a_deleted_object_opens_as_404(self):
        """AC-9001-5: abrir a recuperacao com a versao antiga de um objeto apagado dá 404 sem os dados."""
        listed = self._listed()
        old = [v for v in self.gone_versions if v.pk != listed.pk]
        self.assertTrue(old, msg='fixture: the deleted Author has an older version')
        for version in old:
            with self.subTest(version=version.pk):
                response = self._client_for(self.editor).get(self._url(version.pk))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(FORM_TEMPLATE, [t.name for t in response.templates])
                self._assert_no_foreign_data(response, GONE_MARKERS + ('gone-a-old',))

    def test_old_version_of_a_deleted_object_posts_as_404_and_keeps_it_deleted(self):
        """AC-9001-6: enviar a recuperacao com a versao antiga de um objeto apagado dá 404 e o objeto segue apagado."""
        listed = self._listed()
        old = [v for v in self.gone_versions if v.pk != listed.pk]
        self.assertTrue(old, msg='fixture: the deleted Author has an older version')
        for version in old:
            with self.subTest(version=version.pk):
                response = self._client_for(self.editor).post(self._url(version.pk), POST_DATA)
                self.assertEqual(response.status_code, 404)
                self.assertFalse(Author.objects.filter(pk=self.gone_pk).exists(), msg='the Author must stay deleted')

    def test_without_permission_answers_403(self):
        """AC-9104-4 (controle): sem a permissao de adicionar/alterar/excluir dá 403, tambem com versao que nao existe."""
        for label, version_pk in (('listed version', self._listed().pk), ('missing version', 987654321)):
            for method in ('get', 'post'):
                with self.subTest(version=label, method=method):
                    response = getattr(self._client_for(self.viewer), method)(self._url(version_pk))
                    self.assertEqual(response.status_code, 403)

    def test_missing_numeric_version_answers_404(self):
        """AC-9104-2 (controle): um numero que nenhuma versao tem dá 404 no GET e no POST."""
        for method in ('get', 'post'):
            with self.subTest(method=method):
                response = getattr(self._client_for(self.editor), method)(self._url(987654321))
                self.assertEqual(response.status_code, 404)

    def test_visitor_and_non_staff_get_the_site_login_screen_and_recover_nothing(self):
        """AC-9001-9: visitante e usuario fora da equipe recebem a tela de entrada, sem os dados, sem recuperar."""
        listed = self._listed()
        for label, user in (('visitor', None), ('non-staff user', self.nonstaff)):
            for method in ('get', 'post'):
                with self.subTest(actor=label, method=method):
                    response = getattr(self._client_for(user), method)(self._url(listed.pk), POST_DATA)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn(FORM_TEMPLATE, [t.name for t in response.templates])
                    self._assert_no_foreign_data(response, GONE_MARKERS + ('gone-a-old',))
                    self.assertFalse(Author.objects.filter(pk=self.gone_pk).exists(), msg='the Author must stay deleted')
