# coding=utf-8
"""``UserSettings`` is unique per (user, key), and the migration that makes it so merges the repeated rows. See #8982.

Nothing kept ``UserSettings`` unique per (user, key): two requests that both found no row both created one. The #8930
and the #8983 made the writers and the readers tolerate that (the greatest id wins); the #8982 closes the door: a unique
constraint, a migration (``0005``) that first merges the repeated rows into the one with the greatest id and then
creates the constraint, and the code that creates a row (the ``settings/user`` route and the first opening of the
panel) catching the ``IntegrityError`` the constraint raises when two requests race.

What the module pins:

* the constraint itself (AC-8982-1) and the form of the admin that now refuses a user and a key that exist (AC-8982-9);
* the migration, run through ``MigrationExecutor`` on the SQLite of the suite: the merge keeps the greatest id of each
  group with its value and touches nothing else (AC-8982-2), a table with no repeated row only gets the constraint
  (AC-8982-3), the way back drops the constraint and no row comes back (AC-8982-4), and the merge is logged
  (AC-8982-6). What only MySQL shows (a collation that compares ``site-theme`` and ``Site-Theme`` as equal) is the
  manual AC-8982-5, with its own evidence;
* the two races (AC-8982-7, AC-8982-8). The real race cannot be reproduced on one connection, and the rival cannot be
  inserted inside the savepoint of the code under test (it would be rolled back with the failed insert): the rival row
  exists *before* the code reads, and the code is made to miss it, so the ``IntegrityError`` is the real one;
* what must stay as it was: a key that has a row is updated in place (AC-8982-14). The other regressions of the criterion
  list (AC-8982-10 to AC-8982-13) are in ``test_user_settings_latest.py``.

The migration tests are a ``TransactionTestCase`` (the SQLite schema editor needs the foreign keys switched off, which
it cannot do inside the atomic block of a ``TestCase``) and put the migrations back in ``addCleanup``, so a failure in
the middle does not leave the next test with the table as it was before the ``0005``. The client is built with
``raise_request_exception=False`` so a 500 shows up as a status.

    python runtests.py test_xadmin.test_user_settings_unique
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_user_settings_unique'
"""
import re
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.signals import post_save
from django.test import Client, TestCase, TransactionTestCase

from xadmin.models import UserSettings, UserWidget
from xadmin.views import website

APP = 'xadmin'
BEFORE = [(APP, '0004_normalize_action_flag')]
AFTER = [(APP, '0005_usersettings_unique_user_key')]
# o logger da migração é o `__name__` dela: o carregador a importa por este nome
MIGRATION_LOGGER = 'xadmin.migrations.0005_usersettings_unique_user_key'

POS_KEY = 'dashboard:home:pos'
PANEL_URL = '/xadmin/'
SETTINGS_URL = '/xadmin/settings/user'
ADD_URL = '/xadmin/xadmin/usersettings/add/'


def _user(username, **extra):
    return User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password', **extra)


def _staff(username):
    return _user(username, is_staff=True)


def _superuser(username):
    return _user(username, is_staff=True, is_superuser=True)


def _browser(user):
    client = Client(raise_request_exception=False)
    client.force_login(user)
    return client


class UniqueConstraintTests(TestCase):
    """AC-8982-1: the table refuses a second row of the same user and key."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = _staff('unique-owner')
        cls.other = _staff('unique-other')

    def test_a_second_row_of_the_same_user_and_key_is_refused(self):
        """AC-8982-1: a second row of the same user and key fails with an integrity error and the first one stays."""
        UserSettings.objects.create(user=self.owner, key='some-key', value='first')
        # o `atomic` dentro do `assertRaises`: o erro de integridade desfaz só o savepoint, e o teste segue usando o banco
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserSettings.objects.create(user=self.owner, key='some-key', value='second')
        rows = UserSettings.objects.filter(user=self.owner, key='some-key')
        self.assertEqual(list(rows.values_list('value', flat=True)), ['first'],
                         msg='the user must keep one row of the key, with the value of the first one')

    def test_another_user_or_another_key_may_repeat_the_value(self):
        """AC-8982-1 (controle): the same key of another user, and another key of the same user, are not repeated rows."""
        UserSettings.objects.create(user=self.owner, key='some-key', value='mine')
        UserSettings.objects.create(user=self.other, key='some-key', value='theirs')
        UserSettings.objects.create(user=self.owner, key='another-key', value='mine too')
        self.assertEqual(UserSettings.objects.count(), 3)


class AdminFormTests(TestCase):
    """AC-8982-9: the form of the admin refuses a user and a key that already exist (no code: the constraint of the model)."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = _superuser('form-owner')

    def _add(self, key, value):
        return _browser(self.owner).post(ADD_URL, {'user': self.owner.pk, 'key': key, 'value': value})

    def test_a_user_and_a_key_that_exist_are_refused_with_a_validation_error(self):
        """AC-8982-9: the add form answers 200 with a validation error, not 500, and creates no second row."""
        UserSettings.objects.create(user=self.owner, key='dup', value='first')
        response = self._add('dup', 'second')
        self.assertEqual(response.status_code, 200, msg='a repeated user and key must be a form error, not a 500')
        self.assertIn('already exists', response.content.decode('utf-8', 'replace'),
                      msg='the page must show the validation error of the form')
        self.assertEqual(list(UserSettings.objects.filter(user=self.owner, key='dup').values_list('value', flat=True)),
                         ['first'], msg='no second row may be created, and the first one stays as it was')

    def test_a_new_key_is_saved_by_the_same_form(self):
        """AC-8982-9 (controle): the same form with a key that does not exist saves the row."""
        self._add('fresh', 'value')
        self.assertEqual(UserSettings.objects.get(user=self.owner, key='fresh').value, 'value')


class _MigrationCase(TransactionTestCase):
    """A test that starts with the table as it was before the ``0005`` and puts the migrations back at the end."""

    def setUp(self):
        super().setUp()
        # a restauração entra primeiro em `addCleanup`: vale também se o teste falhar no meio
        self.addCleanup(self._migrate_to_latest)
        self.old_apps = self._migrate(BEFORE)
        self.Old = self.old_apps.get_model(APP, 'UserSettings')
        self.owner = _staff('migration-owner')
        self.other = _staff('migration-other')

    @staticmethod
    def _migrate(targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    @staticmethod
    def _migrate_to_latest():
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _seed(self, rows):
        """Create the rows with the historical model, in order (the first one has the smaller id)."""
        return [self.Old.objects.create(user_id=user.pk, key=key, value=value) for user, key, value in rows]

    @staticmethod
    def _table():
        return list(UserSettings.objects.order_by('pk').values_list('pk', 'user_id', 'key', 'value'))

    def _assert_the_constraint_refuses_a_second_row(self, user, key):
        """The migration really created the unique index: a repeated row fails with an integrity error."""
        with self.assertRaises(IntegrityError, msg='the table must refuse a second row of the same user and key'):
            with transaction.atomic():
                UserSettings.objects.create(user=user, key=key, value='second')


class MergeMigrationTests(_MigrationCase):
    """AC-8982-2, AC-8982-3, AC-8982-4, AC-8982-6: the ``0005`` on the SQLite of the suite."""

    def test_each_group_keeps_the_row_with_the_greatest_id_and_nothing_else_changes(self):
        """AC-8982-2: the repeated rows merge into the greatest id with its value; other users and keys are untouched."""
        a_old, b_old, c_single, a_mid, d_single, b_new, a_new = self._seed([
            (self.owner, POS_KEY, 'a-oldest'),
            (self.owner, 'site-theme', 'b-old'),
            (self.other, POS_KEY, 'c-single'),       # a mesma chave de outro usuário: não é repetida
            (self.owner, POS_KEY, 'a-middle'),
            (self.owner, 'unrelated', 'd-single'),   # outra chave do mesmo usuário: não é repetida
            (self.owner, 'site-theme', 'b-new'),
            (self.owner, POS_KEY, 'a-newest'),
        ])
        self._migrate(AFTER)
        self.assertEqual(self._table(), [
            (c_single.pk, self.other.pk, POS_KEY, 'c-single'),
            (d_single.pk, self.owner.pk, 'unrelated', 'd-single'),
            (b_new.pk, self.owner.pk, 'site-theme', 'b-new'),
            (a_new.pk, self.owner.pk, POS_KEY, 'a-newest'),
        ], msg='each group must keep only its greatest id, with the value it had, and no other row may change')
        self._assert_the_constraint_refuses_a_second_row(self.owner, POS_KEY)

    def test_a_table_with_no_repeated_row_only_gets_the_constraint(self):
        """AC-8982-3: with no repeated row no row is changed, deleted or created, and nothing is logged."""
        self._seed([(self.owner, 'k1', 'v1'), (self.other, 'k1', 'v2'), (self.owner, 'k2', 'v3')])
        before = self._table()
        self.assertEqual(len(before), 3)
        with self.assertNoLogs(MIGRATION_LOGGER, level='INFO'):
            self._migrate(AFTER)
        self.assertEqual(self._table(), before, msg='the rows, their ids and their values must be as they were')
        self._assert_the_constraint_refuses_a_second_row(self.owner, 'k1')

    def test_an_empty_table_only_gets_the_constraint(self):
        """AC-8982-3: an empty table gets the constraint and no row is created."""
        self.assertEqual(self._table(), [])
        self._migrate(AFTER)
        self.assertEqual(self._table(), [], msg='the migration must not create a row')
        UserSettings.objects.create(user=self.owner, key='k1', value='first')
        self._assert_the_constraint_refuses_a_second_row(self.owner, 'k1')

    def test_undoing_the_migration_drops_the_constraint_and_no_row_comes_back(self):
        """AC-8982-4: the way back removes the constraint and changes no row; the rows the merge deleted stay deleted."""
        self._seed([(self.owner, 'dup', 'old'), (self.owner, 'dup', 'new'), (self.other, 'dup', 'single')])
        self._migrate(AFTER)
        # pré-condição positiva: a restrição existe antes da volta, senão "ela some" não prova nada
        self._assert_the_constraint_refuses_a_second_row(self.owner, 'dup')
        merged = self._table()
        # por id: a de maior id do grupo (`new`, id 2) e a do outro usuário (`single`, id 3)
        self.assertEqual([row[3] for row in merged], ['new', 'single'])
        self._migrate(BEFORE)
        self.assertEqual(self._table(), merged, msg='undoing the migration must not change, create or restore a row')
        UserSettings.objects.create(user=self.owner, key='dup', value='again')
        self.assertEqual(UserSettings.objects.filter(user=self.owner, key='dup').count(), 2,
                         msg='without the constraint a second row of the same user and key is accepted again')

    def test_the_merge_logs_how_many_groups_and_rows_it_deleted(self):
        """AC-8982-6: the log carries the number of groups merged and the number of rows deleted."""
        # dois grupos (3 e 2 linhas): 2 grupos juntados e 3 linhas apagadas, dois números diferentes
        self._seed([
            (self.owner, 'group-a', '1'), (self.owner, 'group-a', '2'), (self.owner, 'group-a', '3'),
            (self.owner, 'group-b', '1'), (self.owner, 'group-b', '2'),
            (self.other, 'group-a', 'single'),
        ])
        with self.assertLogs(MIGRATION_LOGGER, level='INFO') as captured:
            self._migrate(AFTER)
        message = ' '.join(record.getMessage() for record in captured.records)
        # cada número ligado à sua palavra (o número que vem antes de «group», o que vem antes de «row»): trocar os
        # argumentos do log («3 groups, 2 rows») não passa
        groups_merged = re.search(r'(\d+)\D*group', message)
        rows_deleted = re.search(r'(\d+)\D*row', message)
        self.assertIsNotNone(groups_merged, msg=f'the log must say how many groups were merged: {message!r}')
        self.assertIsNotNone(rows_deleted, msg=f'the log must say how many rows were deleted: {message!r}')
        self.assertEqual(int(groups_merged.group(1)), 2, msg=f'groups merged in the log: {message!r}')
        self.assertEqual(int(rows_deleted.group(1)), 3, msg=f'rows deleted in the log: {message!r}')


class SettingsRouteRaceTests(TestCase):
    """AC-8982-7 and AC-8982-14: the ``settings/user`` route."""

    KEY = 'race-key'

    @classmethod
    def setUpTestData(cls):
        cls.user = _staff('route-user')
        cls.other = _staff('route-other')

    def _post(self, key, value):
        return _browser(self.user).post(SETTINGS_URL, {'key': key, 'value': value})

    def test_the_second_save_of_a_new_key_finds_the_row_the_first_one_created(self):
        """AC-8982-7: 200 with an empty body, one row of the key, with the value of the second save."""
        # a gravação que chegou antes já criou a linha; a leitura da 2ª não a vê (a corrida), então ela tenta criar e
        # bate na restrição: o `IntegrityError` é o do banco, fora de qualquer savepoint do código sob teste
        UserSettings.objects.create(user=self.user, key=self.KEY, value='first')
        real_filter = UserSettings.objects.filter
        reads_of_the_key = []

        def blind_first_read(*args, **kwargs):
            if kwargs.get('key') == self.KEY:
                reads_of_the_key.append(kwargs)
                if len(reads_of_the_key) == 1:
                    return UserSettings.objects.none()
            return real_filter(*args, **kwargs)

        with mock.patch.object(UserSettings.objects, 'filter', side_effect=blind_first_read):
            response = self._post(self.KEY, 'second')
        self.assertEqual(response.status_code, 200, msg='the second save must not be a 500')
        self.assertEqual(response.content, b'')
        rows = UserSettings.objects.filter(user=self.user, key=self.KEY)
        self.assertEqual(list(rows.values_list('value', flat=True)), ['second'],
                         msg='one row must be left, with the value of the second save')
        # o caminho foi percorrido: a 1ª leitura (cega) e a releitura depois do erro de integridade
        self.assertGreaterEqual(len(reads_of_the_key), 2, msg='the view must read the key again after the integrity error')

    def test_a_key_that_has_a_row_updates_that_row(self):
        """AC-8982-14: a key that already has a row has that row updated, with 200 and an empty body."""
        row = UserSettings.objects.create(user=self.user, key=self.KEY, value='old')
        intruder = UserSettings.objects.create(user=self.other, key=self.KEY, value='keep')
        response = self._post(self.KEY, 'new')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        row.refresh_from_db()
        intruder.refresh_from_db()
        self.assertEqual(row.value, 'new', msg='the row of the user and the key must hold the new value')
        self.assertEqual(UserSettings.objects.filter(user=self.user, key=self.KEY).count(), 1)
        self.assertEqual(intruder.value, 'keep', msg='the row of another user must not change')


class PanelRaceTests(TestCase):
    """AC-8982-8: the first opening of the panel, when another opening creates the default position first."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = _superuser('panel-owner')

    def test_the_second_first_opening_finds_the_position_the_first_one_created(self):
        """AC-8982-8: 200, one row of the position, and the next openings list the widgets of this response."""
        # o painel de teste não define widgets: o `IndexView` ganha dois (um por coluna), criados no `get_init_widget`
        widgets = [[{'type': 'html', 'title': 'TITLEAAA', 'content': 'a'}],
                   [{'type': 'html', 'title': 'TITLEBBB', 'content': 'b'}]]
        rivals = []

        def create_the_rival(sender, instance, created, **kwargs):
            # a abertura que chegou antes grava a posição depois do 1º widget desta: fora do `atomic` do código sob teste.
            # Uma vez só: o `post_save` roda a cada widget criado
            if created and not rivals:
                rivals.append(UserSettings.objects.create(user=instance.user, key=POS_KEY, value=''))

        post_save.connect(create_the_rival, sender=UserWidget, weak=False, dispatch_uid='ac-8982-8-rival')
        self.addCleanup(post_save.disconnect, create_the_rival, sender=UserWidget, dispatch_uid='ac-8982-8-rival')
        browser = _browser(self.owner)
        with mock.patch.object(website.IndexView, 'widgets', widgets):
            first = browser.get(PANEL_URL)
            second = browser.get(PANEL_URL)
        # controle positivo: a rival foi criada, uma só vez, e os dois widgets foram criados por esta abertura
        self.assertEqual(len(rivals), 1, msg='fixture: the rival position must have been created once')
        self.assertEqual(UserWidget.objects.filter(user=self.owner).count(), 2, msg='fixture: both widgets are created')
        self.assertEqual(first.status_code, 200, msg='the first opening must not be a 500')
        self.assertEqual(UserSettings.objects.filter(user=self.owner, key=POS_KEY).count(), 1,
                         msg='one row of the position must be left')
        self.assertEqual(second.status_code, 200)
        shown_first, shown_second = (page.content.decode('utf-8', 'replace') for page in (first, second))
        for title in ('TITLEAAA', 'TITLEBBB'):
            self.assertIn(title, shown_first, msg='the response of the opening lists the widgets it created')
            self.assertIn(title, shown_second, msg='the next opening must list the same widgets')
