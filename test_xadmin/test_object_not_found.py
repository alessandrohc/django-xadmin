# coding=utf-8
"""The password change and the change history answer 404 for an object that does not exist. See #8560.

Before #8560 both views took the ``None`` that ``get_object`` returns for an unknown id
and used it: ``ChangePasswordView`` built ``AdminPasswordChangeForm(None)``
(``AttributeError: 'NoneType' object has no attribute 'has_usable_password'``) and
``RevisionListView`` read ``self.obj.pk``. Every other object view of the package
(update, delete, detail, dashboard) checks the permission first and then raises
``Http404``. These tests pin the same order on the two views: 403 for whoever lacks the
route's permission, whether or not the object exists, and 404 for whoever has it.

The client is built with ``raise_request_exception=False`` so a 500 shows up as a status
to compare, and the red fails on the assertion instead of erroring on the exception.

    python runtests.py test_xadmin.test_object_not_found
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_object_not_found'
"""
import reversion
from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase

from test_xadmin.fixtureapp.models import Author

MISSING_ID = '987654321'


def _staff(username, *codenames):
    """Staff user (xadmin only lets staff in) with exactly the given permissions."""
    user = User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password',
                                    is_staff=True)
    if codenames:
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


class _ClientMixin:
    """A client that turns an unhandled exception into a 500 response instead of raising."""

    def _client_for(self, user):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        return client


class ChangePasswordNotFoundTests(_ClientMixin, TestCase):
    """``ChangePasswordView`` (route ``user_change_password``)."""

    @classmethod
    def setUpTestData(cls):
        cls.changer = _staff('pwd-changer', 'change_user', 'view_user')
        # vê usuários mas não pode alterá-los: distingue "a rota exige alterar" de "exige ver" (has_view_permission é ver OU alterar)
        cls.outsider = _staff('pwd-outsider', 'view_user')
        cls.other = User.objects.create_user('pwd-other', 'pwd-other@example.com', 'old-password-123')

    def _url(self, object_id):
        return f'/xadmin/auth/user/{object_id}/password/'

    def test_unknown_id_answers_404_on_get_and_post(self):
        """AC-8560-1: quem pode alterar usuários e pede um usuário inexistente recebe 404 (GET e POST)."""
        client = self._client_for(self.changer)
        for method in ('get', 'post'):
            with self.subTest(method=method):
                response = getattr(client, method)(self._url(MISSING_ID))
                self.assertEqual(response.status_code, 404,
                                 msg=f'{method.upper()} of an unknown user must answer 404')

    def test_existing_other_user_shows_the_page(self):
        """AC-8560-3: a troca de senha de outro usuário que existe continua mostrando a página."""
        response = self._client_for(self.changer).get(self._url(self.other.pk))
        self.assertEqual(response.status_code, 200)

    def test_user_without_permission_gets_403_whether_or_not_the_user_exists(self):
        """AC-8560-4: sem a permissão de alterar usuários, 403 para usuário existente e inexistente (GET e POST)."""
        client = self._client_for(self.outsider)
        for object_id in (self.other.pk, MISSING_ID):
            for method in ('get', 'post'):
                with self.subTest(object_id=object_id, method=method):
                    response = getattr(client, method)(self._url(object_id))
                    self.assertEqual(response.status_code, 403,
                                     msg='the permission check must come before the existence check')

    def _post_new_password(self):
        return self._client_for(self.changer).post(self._url(self.other.pk), {
            'password1': 'Brand-new-pass-8560',
            'password2': 'Brand-new-pass-8560',
            'usable_password': 'true',
        })

    def test_valid_change_saves_the_new_password(self):
        """AC-8560-7: a troca válida grava a senha nova do outro usuário."""
        self._post_new_password()
        self.other.refresh_from_db()
        self.assertTrue(self.other.check_password('Brand-new-pass-8560'),
                        msg='the new password must be saved')

    def test_valid_change_redirects_to_the_user_edit(self):
        """AC-8560-12: depois da troca válida, a resposta redireciona para a edição do usuário."""
        response = self._post_new_password()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/xadmin/auth/user/{self.other.pk}/update/')


class RevisionListNotFoundTests(_ClientMixin, TestCase):
    """``RevisionListView`` (route ``…_revisionlist``) on the reversion-enabled fixture admin."""

    @classmethod
    def setUpTestData(cls):
        cls.editor = _staff('rev-editor', 'add_author', 'change_author', 'delete_author', 'view_author')
        # um usuário para cada uma das três permissões que falta (xversion.py, has_reversion_permission)
        cls.partials = {
            'no-add': _staff('rev-no-add', 'change_author', 'delete_author', 'view_author'),
            'no-change': _staff('rev-no-change', 'add_author', 'delete_author', 'view_author'),
            'no-delete': _staff('rev-no-delete', 'add_author', 'change_author', 'view_author'),
        }
        with reversion.create_revision():
            cls.author = Author.objects.create(name='first name')
        with reversion.create_revision():
            cls.author.name = 'second name'
            cls.author.save()

    def _url(self, object_id):
        return f'/xadmin/xadmin_fixture/author/{object_id}/revision/'

    def test_unknown_id_answers_404_on_get_and_post(self):
        """AC-8560-2: quem pode adicionar, alterar e excluir e pede um objeto inexistente recebe 404 (GET e POST)."""
        client = self._client_for(self.editor)
        for method in ('get', 'post'):
            with self.subTest(method=method):
                response = getattr(client, method)(self._url(MISSING_ID))
                self.assertEqual(response.status_code, 404,
                                 msg=f'{method.upper()} of an unknown object must answer 404')

    def test_existing_object_shows_the_history(self):
        """AC-8560-3: o histórico de um objeto que existe continua mostrando a página."""
        response = self._client_for(self.editor).get(self._url(self.author.pk))
        self.assertEqual(response.status_code, 200)

    def test_user_without_the_three_permissions_gets_403_whether_or_not_the_object_exists(self):
        """AC-8560-4: sem uma das três permissões, 403 para objeto existente e inexistente (GET e POST)."""
        for missing, user in self.partials.items():
            client = self._client_for(user)
            for object_id in (self.author.pk, MISSING_ID):
                for method in ('get', 'post'):
                    with self.subTest(missing=missing, object_id=object_id, method=method):
                        response = getattr(client, method)(self._url(object_id))
                        self.assertEqual(response.status_code, 403)

    def test_two_different_versions_show_the_comparison(self):
        """AC-8560-8: escolher duas versões diferentes mostra a comparação das duas."""
        from reversion.models import Version
        versions = list(Version.objects.get_for_object(self.author).values_list('pk', flat=True))
        self.assertEqual(len(versions), 2, msg='precondition: the fixture needs two versions')
        response = self._client_for(self.editor).post(
            self._url(self.author.pk), {'version_a': versions[0], 'version_b': versions[1]})
        self.assertEqual(response.status_code, 200)
        self.assertIn('xadmin/views/revision_diff.html', [t.name for t in response.templates],
                      msg='the comparison template must be rendered')
