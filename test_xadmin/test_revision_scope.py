# coding=utf-8
"""A reversao a uma versao so aceita versoes do objeto da rota. See #9002.

``RevisionView.init_request`` buscava a versao por ``get_object_or_404(Version, pk=…, object_id=…)``,
sem o tipo de conteudo: a versao de outro modelo com o mesmo ``object_id`` passava (GET 200, POST 302).
Agora a busca e ``Version.objects.get_for_object(self.org_obj)``; o ``RegistrationError`` de um modelo
sem historico no reversion (o ``Ranked`` do fixture) vira 404, como na comparacao (#8988).

O cliente e montado com ``raise_request_exception=False`` para um 500 aparecer como status.

    python runtests.py test_xadmin.test_revision_scope
    # no container do projeto host:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_revision_scope'
"""
import reversion
from django.test import Client, TestCase
from reversion.models import Version

from test_xadmin.fixtureapp.models import Author, Book, Ranked
from test_xadmin.test_revision_compare_scope import _first_version, _staff

FORM_TEMPLATE = 'xadmin/views/revision_form.html'
# o texto que so as versoes de fora tem: nao pode aparecer em nenhuma resposta da rota do Author A
FOREIGN_MARKERS = ('other-b', 'sb-top-secret', 'book-of-b')
POST_DATA = {'name': 'posted-name', 'secret': 'posted-secret'}


class RevisionScopeTests(TestCase):
    """O admin `Author` do fixture (reversion habilitado) e o `Ranked` (sem reversion)."""

    @classmethod
    def setUpTestData(cls):
        cls.editor = _staff('rscope-editor', 'add_author', 'change_author', 'delete_author', 'view_author')
        cls.viewer = _staff('rscope-viewer', 'view_author', 'change_author')
        cls.ranked_editor = _staff('rscope-ranked', 'add_ranked', 'change_ranked', 'delete_ranked', 'view_ranked')
        with reversion.create_revision():
            cls.author_a = Author.objects.create(name='public-a', secret='secret-of-a')
        with reversion.create_revision():
            cls.author_b = Author.objects.create(name='other-b', secret='sb-top-secret')
        with reversion.create_revision():
            cls.book_same_id = Book.objects.create(author=cls.author_b, title='book-of-b')
        with reversion.create_revision():
            cls.book_other_id = Book.objects.create(author=cls.author_b, title='second-book-of-b')
        cls.ranked = Ranked.objects.create(name='r1', position=1)

    def _client_for(self, user):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        return client

    def _author_url(self, version_pk, author_pk=None):
        return f'/xadmin/xadmin_fixture/author/{author_pk or self.author_a.pk}/revision/{version_pk}/'

    def _foreign_versions(self):
        foreign = {
            'another object of the same model (Author B)': _first_version(self.author_b),
            'another model, same object id (Book)': _first_version(self.book_same_id),
            'another model, another object id (2nd Book)': _first_version(self.book_other_id),
        }
        for label, version in foreign.items():
            self.assertIsNotNone(version, msg=f'fixture: {label} has a version')
        # a colisao que o filtro por `object_id` sozinho deixaria passar
        self.assertEqual(self.book_same_id.pk, self.author_a.pk, msg='fixture: the Book collides with the Author id')
        return foreign

    def _assert_author_a_untouched(self):
        self.author_a.refresh_from_db()
        self.assertEqual((self.author_a.name, self.author_a.secret), ('public-a', 'secret-of-a'),
                         msg='the object must not be saved')

    def test_foreign_versions_open_as_404_and_show_nothing(self):
        """AC-9002-1: abrir a reversao com a versao de outro objeto ou modelo (inclusive o mesmo `object_id`) dá 404."""
        own = _first_version(self.author_a)
        control = self._client_for(self.editor).get(self._author_url(own.pk))
        self.assertEqual(control.status_code, 200, msg='control: the own version opens')
        self.assertIn(FORM_TEMPLATE, [t.name for t in control.templates], msg='control: the revert form is used')
        for label, version in self._foreign_versions().items():
            with self.subTest(version=label):
                response = self._client_for(self.editor).get(self._author_url(version.pk))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(FORM_TEMPLATE, [t.name for t in response.templates])
                body = response.content.decode('utf-8')
                for marker in FOREIGN_MARKERS:
                    self.assertTrue(marker not in body, msg=f'the data of the foreign version must not be shown ({marker})')

    def test_foreign_versions_post_as_404_and_save_nothing(self):
        """AC-9002-2: enviar a reversao com a versao de outro objeto ou modelo dá 404 e não grava."""
        for label, version in self._foreign_versions().items():
            with self.subTest(version=label):
                # os subtestes dividem o banco: um POST que gravou nao pode contaminar o caso seguinte
                Author.objects.filter(pk=self.author_a.pk).update(name='public-a', secret='secret-of-a')
                response = self._client_for(self.editor).post(self._author_url(version.pk), POST_DATA)
                self.assertEqual(response.status_code, 404)
                self._assert_author_a_untouched()

    def test_own_version_opens_the_revert_form(self):
        """AC-9002-3: abrir a reversao com a versao do proprio objeto mostra o formulario."""
        own = _first_version(self.author_a)
        response = self._client_for(self.editor).get(self._author_url(own.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn(FORM_TEMPLATE, [t.name for t in response.templates])

    def test_own_version_post_saves_and_redirects_to_the_change_screen(self):
        """AC-9002-4: enviar a reversao com a versao propria e dados validos grava e redireciona para a tela de alterar."""
        own = _first_version(self.author_a)
        response = self._client_for(self.editor).post(self._author_url(own.pk), POST_DATA)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/xadmin/xadmin_fixture/author/{self.author_a.pk}/update/')
        self.author_a.refresh_from_db()
        self.assertEqual((self.author_a.name, self.author_a.secret), ('posted-name', 'posted-secret'))

    def test_model_without_history_opens_as_404_with_the_version_of_another_model(self):
        """AC-9002-5: no modelo sem reversion, abrir a reversao com a versao de um Author dá 404."""
        author_version = _first_version(self.author_a)
        self.assertIsNotNone(author_version, msg='fixture: a version of another model exists')
        url = f'/xadmin/xadmin_fixture/ranked/{self.ranked.pk}/revision/{author_version.pk}/'
        response = self._client_for(self.ranked_editor).get(url)
        self.assertEqual(response.status_code, 404)

    def test_model_without_history_posts_as_404_with_the_version_of_another_model(self):
        """AC-9002-6: no modelo sem reversion, enviar a reversao com a versao de um Author dá 404."""
        author_version = _first_version(self.author_a)
        self.assertIsNotNone(author_version, msg='fixture: a version of another model exists')
        url = f'/xadmin/xadmin_fixture/ranked/{self.ranked.pk}/revision/{author_version.pk}/'
        response = self._client_for(self.ranked_editor).post(url, {'name': 'x', 'position': 2})
        self.assertEqual(response.status_code, 404)
        self.ranked.refresh_from_db()
        self.assertEqual((self.ranked.name, self.ranked.position), ('r1', 1), msg='the Ranked must not be saved')

    def test_gates_keep_their_order(self):
        """AC-9002-7: sem a permissao de adicionar/alterar/excluir dá 403; objeto da rota inexistente dá 404."""
        foreign = _first_version(self.book_same_id)
        own = _first_version(self.author_a)
        for method in ('get', 'post'):
            with self.subTest(gate='no permission, foreign version', method=method):
                response = getattr(self._client_for(self.viewer), method)(self._author_url(foreign.pk))
                self.assertEqual(response.status_code, 403)
            with self.subTest(gate='missing object', method=method):
                response = getattr(self._client_for(self.editor), method)(self._author_url(own.pk, author_pk=987654))
                self.assertEqual(response.status_code, 404)
