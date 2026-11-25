# coding=utf-8
"""A password change saved by the xadmin leaves an entry in the audit log (``xadmin.models.Log``). See #9134.

Before #9134 neither ``ChangePasswordView.form_valid`` (another user's password, route
``user_change_password``) nor ``ChangeAccountPasswordView.form_valid`` (the own password,
route ``account/password/``) called ``self.log``: the password was saved (302) and the log
stayed empty, while editing the same user left one row. These tests pin what the log must
say after a saved change (one entry, who changed what, from which IP, never the password
nor its hash), that a refused form leaves nothing, and that a failure while writing the
entry keeps the change and the redirect (``AuditLog._log`` swallows it, xadmin/auditlog.py:25-34).

The five cases of AC-9134-1 (another user, a superuser target, the change that turns the
password unusable, the actor changing their own password through the other-user route, and
the own-password route) are looped with ``subTest``; every case builds its own users.
Every negative assertion carries its positive precondition in the same test: the change was
saved, or the same request with valid data is recorded.

The client is built with ``raise_request_exception=False`` so a 500 shows up as a status to
compare. The password hasher is the fast MD5 one: only the stored value matters here.

    python runtests.py test_xadmin.test_password_change_audit
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_password_change_audit'
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings

from xadmin.models import Log

# textos raros: não aparecem em nenhum outro campo da linha do log (nome de usuário, e-mail, mensagem)
OLD_PASSWORD = 'Zq7-rare-OLD-9134-pw'
NEW_PASSWORD = 'Zq7-rare-NEW-9134-pw'
WRONG_OLD_PASSWORD = 'Zq7-rare-WRONG-9134'
FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# os cinco casos do AC-9134-1
CASES = ('common-user', 'superuser-target', 'unusable-password', 'self-via-other-route', 'own-route')


def _user(username, *codenames, superuser=False, staff=True):
    """User with exactly the given permissions (a superuser has all of them)."""
    email = f'{username}@example.com'
    if superuser:
        return User.objects.create_superuser(username, email, OLD_PASSWORD)
    user = User.objects.create_user(username, email, OLD_PASSWORD, is_staff=staff)
    if codenames:
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def _case(name, tag):
    """Builds one case with fresh users (the user names are unique per ``name`` and ``tag``).

    ``new_password`` is ``None`` when the change turns the target's password unusable.
    ``location`` is where the saved change redirects to.
    """
    label = f'{name}-{tag}'
    if name == 'own-route':
        actor = _user(f'{label}-actor')
        return SimpleNamespace(
            name=name, actor=actor, target=actor, url='/xadmin/account/password/',
            data={'old_password': OLD_PASSWORD, 'new_password1': NEW_PASSWORD,
                  'new_password2': NEW_PASSWORD},
            new_password=NEW_PASSWORD, location='/xadmin/')
    if name == 'superuser-target':
        actor = _user(f'{label}-actor', superuser=True)
        target = _user(f'{label}-target', superuser=True)
    elif name == 'self-via-other-route':
        actor = target = _user(f'{label}-actor', 'change_user', 'view_user')
    else:  # common-user e unusable-password
        actor = _user(f'{label}-actor', 'change_user', 'view_user')
        target = _user(f'{label}-target', staff=False)
    if name == 'unusable-password':
        # o formulário do admin envia os dois campos vazios quando a senha vira inutilizável
        data = {'password1': '', 'password2': '', 'usable_password': 'false'}
        new_password = None
    else:
        data = {'password1': NEW_PASSWORD, 'password2': NEW_PASSWORD, 'usable_password': 'true'}
        new_password = NEW_PASSWORD
    return SimpleNamespace(
        name=name, actor=actor, target=target, url=f'/xadmin/auth/user/{target.pk}/password/',
        data=data, new_password=new_password, location=f'/xadmin/auth/user/{target.pk}/update/')


def _post(case, data=None):
    """POST of the case as its actor, with the case's valid data unless ``data`` is given."""
    client = Client(raise_request_exception=False)
    client.force_login(case.actor)
    return client.post(case.url, case.data if data is None else data)


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class _AuditTestCase(TestCase):

    def _log_ids(self):
        return set(Log.objects.values_list('pk', flat=True))

    def _entries_since(self, ids):
        return list(Log.objects.exclude(pk__in=ids).order_by('pk'))

    def _assert_change_saved(self, case):
        """A troca foi gravada: a senha nova confere, ou a senha ficou inutilizável."""
        case.target.refresh_from_db()
        if case.new_password is None:
            self.assertFalse(case.target.has_usable_password(),
                             msg=f'{case.name}: the password must be unusable after the change')
        else:
            self.assertTrue(case.target.check_password(case.new_password),
                            msg=f'{case.name}: the new password must be saved')

    def _post_saved(self, case):
        """Posts the valid data and checks the change was saved (302); returns the response."""
        response = _post(case)
        self.assertEqual(response.status_code, 302, msg=f'{case.name}: precondition: the change must be saved')
        self._assert_change_saved(case)
        return response

    def _post_with_failing_log_write(self, case):
        """POST with the write of the ``Log`` row raising a Python exception.

        Não é um erro de banco: dentro do ``atomic`` do TestCase ele marcaria o rollback do teste
        (a view real não abre transação, ``auth.py`` ``post`` não é atômico). O ``AuditLog._log``
        engole a exceção e loga ``AuditLog error`` (xadmin/auditlog.py:25-34), então a tentativa de
        gravação (``save`` chamado) e o erro logado provam que a falha foi exercida.
        """
        with patch.object(Log, 'save', side_effect=RuntimeError('boom')) as save:
            with self.assertLogs('xadmin.auditlog', level='ERROR') as logs:
                response = _post(case)
        return response, save, logs

    def _assert_failure_was_exercised(self, case, save, logs):
        self.assertEqual(save.call_count, 1, msg=f'{case.name}: the write of the log row must have been attempted once')
        self.assertTrue(any('AuditLog error' in line for line in logs.output),
                        msg=f'{case.name}: the failed write must be logged as "AuditLog error"')


class PasswordChangeAuditTests(_AuditTestCase):
    """The entry a saved change leaves in the audit log."""

    def test_saved_change_adds_one_audit_entry(self):
        """AC-9134-1: a troca gravada deixa exatamente uma entrada: quem trocou, o IP, o usuário cuja senha mudou."""
        user_content_type = ContentType.objects.get_for_model(User)
        for name in CASES:
            with self.subTest(case=name):
                case = _case(name, 'one-entry')
                ids = self._log_ids()
                self._post_saved(case)  # pré-condição: a troca foi gravada
                entries = self._entries_since(ids)
                self.assertEqual(len(entries), 1, msg=f'{name}: a saved change must add exactly one audit entry')
                entry = entries[0]
                self.assertEqual(entry.user_id, case.actor.pk, msg=f'{name}: the entry must name who changed it')
                self.assertEqual(entry.ip_addr, '127.0.0.1', msg=f'{name}: the entry must carry the request IP')
                self.assertEqual(entry.content_type, user_content_type, msg=f'{name}: the object is a user')
                self.assertEqual(entry.object_id, str(case.target.pk),
                                 msg=f'{name}: the object must be the user whose password changed')
                self.assertIn('password', entry.message, msg=f'{name}: the message must say it is the password')
                # a lista do log mostra a linha como alteração do usuário (models.py Log.__str__, bandeira update)
                self.assertTrue(str(entry).startswith(f'Changed "{case.target}"'),
                                msg=f'{name}: the log list must show a change of the user, got {str(entry)!r}')

    def test_entry_never_holds_a_password_or_its_hash(self):
        """AC-9134-2: nenhum campo da entrada tem a senha nova, a antiga nem o valor guardado de nenhuma."""
        for name in CASES:
            with self.subTest(case=name):
                case = _case(name, 'no-secret')
                hash_before = case.target.password
                ids = self._log_ids()
                self._post_saved(case)  # pré-condição: a troca foi gravada
                hash_after = case.target.password
                self.assertNotEqual(hash_before, hash_after, msg=f'{name}: precondition: the stored value must change')
                entries = self._entries_since(ids)
                self.assertEqual(len(entries), 1, msg=f'{name}: precondition: the change must have an entry to scan')
                secrets = [OLD_PASSWORD, hash_before, hash_after]
                if case.new_password is not None:
                    secrets.append(case.new_password)
                entry = entries[0]
                # todos os campos do modelo, lidos do modelo; mais a forma que a lista do log mostra
                texts = {field.attname: str(getattr(entry, field.attname)) for field in Log._meta.concrete_fields}
                texts['__str__'] = str(entry)
                for field_name, text in texts.items():
                    for secret in secrets:
                        self.assertNotIn(secret, text, msg=f'{name}: the {field_name} of the entry holds a password or a hash')


class RefusedChangeTests(_AuditTestCase):
    """A change the form refuses leaves no entry."""

    # (rótulo, caso, dados recusados): as senhas diferentes e a senha curta demais nas duas rotas, e a senha antiga errada
    REFUSALS = (
        ('other user, passwords differ', 'common-user',
         {'password1': NEW_PASSWORD, 'password2': NEW_PASSWORD + '-x', 'usable_password': 'true'}),
        ('other user, password too short', 'common-user',
         {'password1': 'short', 'password2': 'short', 'usable_password': 'true'}),
        ('own password, passwords differ', 'own-route',
         {'old_password': OLD_PASSWORD, 'new_password1': NEW_PASSWORD, 'new_password2': NEW_PASSWORD + '-x'}),
        ('own password, password too short', 'own-route',
         {'old_password': OLD_PASSWORD, 'new_password1': 'short', 'new_password2': 'short'}),
        ('own password, wrong old password', 'own-route',
         {'old_password': WRONG_OLD_PASSWORD, 'new_password1': NEW_PASSWORD, 'new_password2': NEW_PASSWORD}),
    )

    # a validação de tamanho do Django, ligada só aqui: o test_xadmin/settings.py não traz validadores
    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}}])
    def test_refused_change_adds_no_audit_entry(self):
        """AC-9134-3: o formulário recusa a troca, por qualquer motivo, e nenhuma entrada é registrada."""
        for index, (label, name, refused_data) in enumerate(self.REFUSALS):
            with self.subTest(refusal=label):
                case = _case(name, f'refused-{index}')
                hash_before = case.target.password
                ids = self._log_ids()
                response = _post(case, refused_data)
                self.assertEqual(response.status_code, 200, msg=f'{label}: the refused change must show the form again')
                case.target.refresh_from_db()
                self.assertEqual(case.target.password, hash_before, msg=f'{label}: precondition: the password must not change')
                self.assertEqual(self._entries_since(ids), [], msg=f'{label}: a refused change must leave no audit entry')
                # controle positivo: o mesmo pedido com os dados certos grava e registra (senão o "nenhuma entrada" é vazio)
                control = _case(name, f'control-{index}')
                control_ids = self._log_ids()
                self._post_saved(control)
                self.assertEqual(len(self._entries_since(control_ids)), 1,
                                 msg=f'{label}: positive control: the same request with valid data must be recorded')


class FailedLogWriteTests(_AuditTestCase):
    """A failure while writing the entry keeps the change and the redirect."""

    def test_failed_log_write_keeps_the_saved_change(self):
        """AC-9134-4: a gravação da entrada falha e a troca continua gravada."""
        for name in CASES:
            with self.subTest(case=name):
                case = _case(name, 'failing-keeps')
                ids = self._log_ids()
                response, save, logs = self._post_with_failing_log_write(case)
                self._assert_failure_was_exercised(case, save, logs)
                self._assert_change_saved(case)
                self.assertEqual(self._entries_since(ids), [], msg=f'{name}: no entry can exist when its write failed')
                # controle positivo: sem a falha, o mesmo pedido registra (o AC-9134-1)
                control = _case(name, 'ctrl-keeps')
                control_ids = self._log_ids()
                self._post_saved(control)
                self.assertEqual(len(self._entries_since(control_ids)), 1,
                                 msg=f'{name}: positive control: without the failure the same request is recorded')

    def test_failed_log_write_keeps_the_redirect(self):
        """AC-9134-7: a gravação da entrada falha e a resposta é o mesmo redirecionamento da troca sem falha."""
        for name in CASES:
            with self.subTest(case=name):
                case = _case(name, 'failing-redirect')
                response, save, logs = self._post_with_failing_log_write(case)
                self._assert_failure_was_exercised(case, save, logs)
                self.assertEqual(response.status_code, 302, msg=f'{name}: the response must still be a redirect')
                self.assertEqual(response['Location'], case.location, msg=f'{name}: the redirect must go where it goes without the failure')
                # controle positivo: sem a falha, o mesmo pedido registra e redireciona para o mesmo lugar
                control = _case(name, 'ctrl-redirect')
                control_ids = self._log_ids()
                control_response = self._post_saved(control)
                self.assertEqual(control_response['Location'], control.location,
                                 msg=f'{name}: positive control: the redirect without the failure')
                self.assertEqual(len(self._entries_since(control_ids)), 1,
                                 msg=f'{name}: positive control: without the failure the same request is recorded')


class OwnPasswordChangeTests(_AuditTestCase):
    """The own-password route (``account/password/``) already saved and redirected; it had no test."""

    def test_own_password_change_saves_the_new_password(self):
        """AC-9134-5 (controle): quem troca a própria senha, com a senha antiga certa, tem a senha nova gravada."""
        case = _case('own-route', 'saves')
        response = _post(case)
        self.assertEqual(response.status_code, 302, msg='precondition: the change must be accepted')
        case.target.refresh_from_db()
        self.assertTrue(case.target.check_password(NEW_PASSWORD), msg='the new password must be saved')
        self.assertFalse(case.target.check_password(OLD_PASSWORD), msg='the old password must stop working')

    def test_own_password_change_redirects_to_the_index(self):
        """AC-9134-6 (controle): depois da troca da própria senha, a resposta redireciona para o índice do xadmin."""
        case = _case('own-route', 'redirects')
        response = _post(case)
        self.assertEqual(response.status_code, 302, msg='precondition: the change must be accepted')
        self.assertEqual(response['Location'], '/xadmin/')
