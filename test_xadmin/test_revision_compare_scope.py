# coding=utf-8
"""A comparação de versões do histórico só aceita versões do objeto da rota. See #8988.

``RevisionListView.post`` buscava as duas versões por ``get_object_or_404(Version, pk=…)``, sem
restringir ao objeto da rota nem ao modelo: a versão de outro objeto do mesmo modelo dava 200 com o
diff dos dados dele, e a de outro modelo dava 500 (``AttributeError`` no ``_get_diffs``, porque o
``Book`` não tem um campo do ``Author``). Agora as duas versões vêm de
``Version.objects.get_for_object(self.obj)``, e o ``RegistrationError`` que o reversion levanta num
modelo sem histórico (o ``Ranked`` do fixture) vira 404, como a versão que ninguém tem sempre foi.

O cliente é montado com ``raise_request_exception=False`` para um 500 aparecer como status e o red
cair no assert.

    python runtests.py test_xadmin.test_revision_compare_scope
    # no container do projeto host:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_revision_compare_scope'
"""
import reversion
from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from reversion.models import Version

from test_xadmin.fixtureapp.models import Author, Book, Ranked

DIFF_TEMPLATE = 'xadmin/views/revision_diff.html'
MISSING_ID = '987654321'
# o texto que só as versões de fora têm: não pode aparecer em nenhuma resposta da rota do Author A
FOREIGN_MARKERS = ('other-b', 'sb-top-secret', 'book-of-b')


def _staff(username, *codenames):
    """Usuário da equipe (o xadmin só deixa a equipe entrar) com exatamente as permissões dadas."""
    user = User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password',
                                    is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def _first_version(obj):
    return Version.objects.get_for_object(obj).order_by('pk').first()


class CompareScopeTests(TestCase):
    """O admin `Author` do fixture (reversion habilitado; o `Book` entra como inline dele) e o `Ranked` (sem reversion)."""

    @classmethod
    def setUpTestData(cls):
        cls.editor = _staff('scope-editor', 'add_author', 'change_author', 'delete_author', 'view_author')
        cls.ranked_editor = _staff('scope-ranked', 'add_ranked', 'change_ranked', 'delete_ranked', 'view_ranked')
        with reversion.create_revision():
            cls.author_a = Author.objects.create(name='public-a', secret='secret-of-a')
        with reversion.create_revision():
            cls.author_a.name = 'public-a-v2'
            cls.author_a.save()
        with reversion.create_revision():
            cls.author_a.name = 'public-a-v3'
            cls.author_a.save()
        with reversion.create_revision():
            cls.author_b = Author.objects.create(name='other-b', secret='sb-top-secret')
        with reversion.create_revision():
            cls.book_same_id = Book.objects.create(author=cls.author_b, title='book-of-b')
        with reversion.create_revision():
            cls.book_other_id = Book.objects.create(author=cls.author_b, title='second-book-of-b')
        cls.ranked = Ranked.objects.create(name='r1', position=1)

    def _client_for(self, user):
        """Cliente que vira o 500 em resposta; o nome não é `client`, que é o do `TestCase`."""
        client = Client(raise_request_exception=False)
        client.force_login(user)
        return client

    def _compare(self, version_a, version_b):
        url = f'/xadmin/xadmin_fixture/author/{self.author_a.pk}/revision/'
        return self._client_for(self.editor).post(url, {'version_a': version_a, 'version_b': version_b})

    def test_versions_of_another_object_or_model_answer_404_and_show_nothing(self):
        """AC-8988-1: a versão de outro objeto, do mesmo modelo ou de outro (inclusive com o mesmo `object_id`), dá 404."""
        own = list(Version.objects.get_for_object(self.author_a).order_by('pk'))
        self.assertGreaterEqual(len(own), 3, msg='fixture: the Author A has its three versions')
        # a colisão que o filtro por `object_id` sozinho deixaria passar: o Book e o Author A têm o mesmo pk
        self.assertEqual(self.book_same_id.pk, self.author_a.pk, msg='fixture: the Book collides with the Author id')
        # controle positivo: com duas versões do próprio objeto a comparação existe e mostra os dados das duas
        # (sem ele, o «404 sem os dados» abaixo passaria vazio)
        control = self._compare(own[0].pk, own[-1].pk)
        self.assertEqual(control.status_code, 200, msg='fixture: the own versions are compared')
        self.assertIn(DIFF_TEMPLATE, [t.name for t in control.templates], msg='fixture: the diff page is used')
        self.assertTrue('public-a-v3' in control.content.decode('utf-8'), msg='fixture: the diff shows the data')

        foreign = {
            'another object of the same model (Author B)': _first_version(self.author_b),
            'another model, same object id (Book)': _first_version(self.book_same_id),
            'another model, another object id (2nd Book)': _first_version(self.book_other_id),
        }
        for label, version in foreign.items():
            self.assertIsNotNone(version, msg=f'fixture: {label} has a version')
        positions = {
            'version_b is foreign': lambda f: (own[0].pk, f.pk),
            'version_a is foreign': lambda f: (f.pk, own[0].pk),
        }
        cases = [(label, where, build(version))
                 for label, version in foreign.items() for where, build in positions.items()]
        both = (_first_version(self.author_b).pk, _first_version(self.book_same_id).pk)
        cases.append(('both foreign', 'both', both))
        for label, where, (a, b) in cases:
            with self.subTest(version=label, where=where):
                response = self._compare(a, b)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(DIFF_TEMPLATE, [t.name for t in response.templates])
                body = response.content.decode('utf-8')
                for marker in FOREIGN_MARKERS:
                    self.assertTrue(marker not in body, msg=f'the data of the foreign version must not be shown ({marker})')

    def test_model_without_history_answers_404_to_the_version_of_another_model(self):
        """AC-8988-1: num modelo sem reversion, as versões de outro modelo (existentes) dão 404 e não 500."""
        versions = list(Version.objects.get_for_object(self.author_a).order_by('pk'))
        self.assertGreaterEqual(len(versions), 2, msg='fixture: two versions of another model exist')
        url = f'/xadmin/xadmin_fixture/ranked/{self.ranked.pk}/revision/'
        response = self._client_for(self.ranked_editor).post(
            url, {'version_a': versions[0].pk, 'version_b': versions[-1].pk})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(DIFF_TEMPLATE, [t.name for t in response.templates])

    def test_model_without_history_keeps_answering_404_to_versions_nobody_has(self):
        """AC-8930-16 (controle): num modelo sem reversion, versões que ninguém tem seguem dando 404 (não o 500 do registro)."""
        url = f'/xadmin/xadmin_fixture/ranked/{self.ranked.pk}/revision/'
        response = self._client_for(self.ranked_editor).post(
            url, {'version_a': MISSING_ID, 'version_b': str(int(MISSING_ID) + 1)})
        self.assertEqual(response.status_code, 404)
