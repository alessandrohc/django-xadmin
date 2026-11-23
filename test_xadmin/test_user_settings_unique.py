# coding=utf-8
"""``UserSettings`` is kept one row per (user, key) in Python, not by a constraint of the database. See #9083, #8982.

The #8982 put a unique constraint on (user, key) and a migration (``0005``) that merged the repeated rows. The #9083
takes both out (no installation ever received the ``0005``: the host projects are legacy databases that cannot be
asked to run it): the rule is kept by the code that creates a row, which locks the row of the user inside an
``atomic`` block, reads the rows of the key again with a locking read and only then creates, and the tolerant readers
of the #8983 (the greatest id wins) stay for the duplicates the old databases already have.

What the module pins:

* the migrations of the xadmin create no unique constraint on (user, key) (AC-9083-1), and running them on a database
  where a user already has two rows of a key finishes and changes no row (AC-9083-8);
* the form of the admin: a user and a key that exist are refused, also when an existing row is changed to that pair
  (AC-8982-9), and a row that was already a duplicate is saved without a validation error while its pair does not
  change (AC-9083-9);
* the order of the lock of the two creating paths (the ``settings/user`` route and the first opening of the panel):
  the lock of the row of the user, the locking re-read, and only then the insert; a row created by a rival while the
  lock was awaited is used and not duplicated. These tests are *reinforcement*, not the proof of the criteria
  AC-9083-2 and AC-9083-7: on the SQLite of this suite ``select_for_update`` does nothing (it is ignored), so they
  check that the lock was asked for, in order, not that it blocked. The real race is the manual evidence of those two
  criteria, on the MySQL of the dev;
* what must stay as it was: a key that has a row is updated in place (AC-8982-14). The other regressions of the
  criterion list are in ``test_user_settings_latest.py``.

The migration tests are a ``TransactionTestCase`` (the SQLite schema editor needs the foreign keys switched off, which
it cannot do inside the atomic block of a ``TestCase``) and put the migrations back in ``addCleanup``. The client is
built with ``raise_request_exception=False`` so a 500 shows up as a status.

    python runtests.py test_xadmin.test_user_settings_unique
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_user_settings_unique'
"""
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.query import QuerySet
from django.db.models.signals import post_save
from django.test import Client, TestCase, TransactionTestCase

from xadmin.models import UserSettings, UserWidget
from xadmin.views import website

APP = 'xadmin'
BEFORE = [(APP, '0004_normalize_action_flag')]

POS_KEY = 'dashboard:home:pos'
PANEL_URL = '/xadmin/'
SETTINGS_URL = '/xadmin/settings/user'
ADD_URL = '/xadmin/xadmin/usersettings/add/'
UPDATE_URL = '/xadmin/xadmin/usersettings/%d/update/'


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


def _repeat(case, user, key, values):
    """Build rows of the same user and key (the legacy state). While the table has a unique constraint this fails."""
    rows = []
    for value in values:
        try:
            with transaction.atomic():
                rows.append(UserSettings.objects.create(user=user, key=key, value=value))
        except IntegrityError:
            case.fail('fixture: two rows of the same user and key cannot be built, the table still has the unique '
                      'constraint (the #9083 takes it out)')
    return rows


def _unique_on_user_and_key():
    """The unique constraints of ``xadmin_usersettings`` that cover exactly the columns user and key."""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, UserSettings._meta.db_table)
    return [name for name, data in constraints.items()
            if data['unique'] and not data['primary_key'] and set(data['columns']) == {'user_id', 'key'}]


class NoConstraintTests(TestCase):
    """AC-9083-1: the migrations of the xadmin create no unique constraint on (user, key)."""

    def test_the_table_has_no_unique_constraint_on_the_user_and_the_key(self):
        """AC-9083-1: after the migrations of a new database no unique constraint covers user and key."""
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, UserSettings._meta.db_table)
        # pré-condição positiva: a introspecção enxerga a tabela (a chave primária) e o índice da chave estrangeira
        self.assertTrue(any(data['primary_key'] for data in constraints.values()),
                        msg='fixture: the introspection must see the primary key of the table')
        self.assertTrue(any(data['columns'] == ['user_id'] for data in constraints.values()),
                        msg='fixture: the introspection must see the index of the foreign key')
        self.assertEqual(_unique_on_user_and_key(), [],
                         msg='no unique constraint may cover the columns user and key')


class AdminFormTests(TestCase):
    """AC-8982-9, AC-9083-9: the validation of the admin form is the ``clean`` of the model, no constraint."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = _superuser('form-owner')

    def _add(self, key, value):
        return _browser(self.owner).post(ADD_URL, {'user': self.owner.pk, 'key': key, 'value': value})

    def _change(self, row, key, value):
        return _browser(self.owner).post(UPDATE_URL % row.pk, {'user': row.user_id, 'key': key, 'value': value})

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

    def test_changing_a_row_to_the_pair_of_another_row_is_refused(self):
        """AC-8982-9: an existing row changed to a user and a key that another row has is a validation error."""
        UserSettings.objects.create(user=self.owner, key='taken', value='first')
        other = UserSettings.objects.create(user=self.owner, key='mine', value='second')
        response = self._change(other, 'taken', 'changed')
        self.assertEqual(response.status_code, 200, msg='the change to a taken pair must be a form error, not a 500')
        self.assertIn('already exists', response.content.decode('utf-8', 'replace'))
        other.refresh_from_db()
        self.assertEqual((other.key, other.value), ('mine', 'second'), msg='the refused change must not be saved')

    def test_a_row_that_was_already_a_duplicate_is_saved_when_its_pair_does_not_change(self):
        """AC-9083-9: with two rows of the key from before, editing the value of one of them saves, without an error."""
        old, new = _repeat(self, self.owner, 'legacy', ['old', 'new'])
        response = self._change(old, 'legacy', 'edited')
        self.assertEqual(response.status_code, 302, msg='the form must save the row and redirect, not show an error')
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual((old.value, new.value), ('edited', 'new'), msg='only the edited row changes')

    def test_a_new_row_is_still_refused_next_to_a_legacy_duplicate(self):
        """AC-9083-9 (controle): the exception is for the existing row only; a third row of the pair is refused."""
        _repeat(self, self.owner, 'legacy', ['old', 'new'])
        response = self._add('legacy', 'third')
        self.assertEqual(response.status_code, 200)
        self.assertIn('already exists', response.content.decode('utf-8', 'replace'))
        self.assertEqual(UserSettings.objects.filter(user=self.owner, key='legacy').count(), 2)


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
    def _leaf():
        """The last migration of the xadmin in the graph on disk: the ``0004`` once the ``0005`` is gone."""
        return [node for node in MigrationExecutor(connection).loader.graph.leaf_nodes() if node[0] == APP]

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


class LegacyDuplicatesMigrationTests(_MigrationCase):
    """AC-9083-8: the ``migrate`` of the xadmin on a database where a user has two rows of a key."""

    def test_migrating_with_repeated_rows_finishes_and_changes_no_row(self):
        """AC-9083-8: the repeated rows, their ids and their values are as they were after the migrations."""
        self._seed([
            (self.owner, POS_KEY, 'a-oldest'),
            (self.owner, 'site-theme', 'b-old'),
            (self.other, POS_KEY, 'c-single'),
            (self.owner, POS_KEY, 'a-newest'),
            (self.owner, 'site-theme', 'b-new'),
        ])
        before = self._table()
        # pré-condição positiva: há mesmo linhas repetidas antes (o estado legado), e a tabela tem 5 linhas
        self.assertEqual(len(before), 5)
        self.assertEqual(len({(row[1], row[2]) for row in before}), 3, msg='fixture: two keys must be repeated')
        self._migrate(self._leaf())
        self.assertEqual(self._table(), before,
                         msg='the migrations must not merge, delete, change or create a row of a database with '
                             'repeated rows')


class SettingsRouteLockTests(TestCase):
    """AC-8982-14 and the order of the lock of the ``settings/user`` route (reinforcement of AC-9083-2)."""

    KEY = 'race-key'

    @classmethod
    def setUpTestData(cls):
        cls.user = _staff('route-user')
        cls.other = _staff('route-other')

    def _post(self, key, value):
        return _browser(self.user).post(SETTINGS_URL, {'key': key, 'value': value})

    def _spy(self, rival_on_lock=None):
        """Record, in order, the locks asked for and the inserts of ``UserSettings``.

        ``rival_on_lock``: a callable run the first time the row of the user is locked, to create the row a rival
        request committed while this one waited for the lock.
        """
        events = []
        original = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            events.append(f'lock:{queryset.model.__name__}')
            if rival_on_lock is not None and queryset.model is User and events.count('lock:User') == 1:
                rival_on_lock()
            return original(queryset, *args, **kwargs)

        def on_insert(sender, instance, created, **kwargs):
            if created:
                events.append('insert')

        post_save.connect(on_insert, sender=UserSettings, weak=False, dispatch_uid='9083-route-insert')
        self.addCleanup(post_save.disconnect, sender=UserSettings, dispatch_uid='9083-route-insert')
        patcher = mock.patch.object(QuerySet, 'select_for_update', spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return events

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

    def test_creating_a_row_locks_the_user_then_rereads_with_a_lock_then_inserts(self):
        """AC-9083-2: reinforcement (the real race is the manual evidence on the MySQL); the lock of the user, the locking re-read, and only then the insert, in order."""
        events = self._spy()
        response = self._post(self.KEY, 'first')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(events, ['lock:User', 'lock:UserSettings', 'insert'],
                         msg='the route must lock the row of the user, re-read the key with a lock, and then insert')
        self.assertEqual(list(UserSettings.objects.filter(user=self.user, key=self.KEY).values_list('value', flat=True)),
                         ['first'])

    def test_updating_a_row_takes_no_lock(self):
        """AC-9083-2 (controle): reinforcement (the real race is the manual evidence on the MySQL); the path that finds the row only updates it, with no lock."""
        UserSettings.objects.create(user=self.user, key=self.KEY, value='old')
        events = self._spy()
        self._post(self.KEY, 'new')
        self.assertEqual(events, [], msg='a key that has a row must be updated without asking for a lock')

    def test_a_row_created_by_a_rival_while_the_lock_was_awaited_is_used_and_not_duplicated(self):
        """AC-9083-2: reinforcement (the real race is the manual evidence on the MySQL); the locking re-read finds the rival row, nothing is inserted, one row is left."""
        rivals = []

        def rival():
            rivals.append(UserSettings.objects.create(user=self.user, key=self.KEY, value='rival'))

        events = self._spy(rival_on_lock=rival)
        response = self._post(self.KEY, 'second')
        self.assertEqual(len(rivals), 1, msg='fixture: the rival row must have been created while the lock was awaited')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        # o único `insert` é o do rival; o código sob teste não inseriu nada
        self.assertEqual(events.count('insert'), 1, msg='only the rival may insert: the route must use its row')
        self.assertEqual(list(UserSettings.objects.filter(user=self.user, key=self.KEY).values_list('value', flat=True)),
                         ['second'], msg='one row must be left, with the value of this save')


class PanelLockTests(TestCase):
    """The order of the lock of the first opening of the panel (reinforcement of AC-9083-7)."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = _superuser('panel-owner')

    def _open(self, rival_on_lock=None):
        # o painel de teste não define widgets: o `IndexView` ganha dois (um por coluna), criados no `get_init_widget`
        widgets = [[{'type': 'html', 'title': 'TITLEAAA', 'content': 'a'}],
                   [{'type': 'html', 'title': 'TITLEBBB', 'content': 'b'}]]
        events = []
        original = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            events.append(f'lock:{queryset.model.__name__}')
            if rival_on_lock is not None and queryset.model is User and events.count('lock:User') == 1:
                rival_on_lock()
            return original(queryset, *args, **kwargs)

        def on_insert(sender, instance, created, **kwargs):
            if created and instance.key == POS_KEY:
                events.append('insert-position')

        post_save.connect(on_insert, sender=UserSettings, weak=False, dispatch_uid='9083-panel-insert')
        self.addCleanup(post_save.disconnect, sender=UserSettings, dispatch_uid='9083-panel-insert')
        with mock.patch.object(QuerySet, 'select_for_update', spy), \
                mock.patch.object(website.IndexView, 'widgets', widgets):
            response = _browser(self.owner).get(PANEL_URL)
        return response, events

    def test_the_first_opening_locks_the_user_then_rereads_with_a_lock_then_inserts_the_position(self):
        """AC-9083-7: reinforcement (the real race is the manual evidence on the MySQL); lock of the user, locking re-read of the position, then the insert."""
        response, events = self._open()
        self.assertEqual(response.status_code, 200)
        relevant = [event for event in events if event in ('lock:User', 'lock:UserSettings', 'insert-position')]
        self.assertEqual(relevant, ['lock:User', 'lock:UserSettings', 'insert-position'],
                         msg='the panel must lock the row of the user, re-read the position with a lock, then insert')
        self.assertEqual(UserSettings.objects.filter(user=self.owner, key=POS_KEY).count(), 1)

    def test_a_position_created_by_a_rival_while_the_lock_was_awaited_is_used_and_not_duplicated(self):
        """AC-9083-7: reinforcement (the real race is the manual evidence on the MySQL); the locking re-read finds the rival position, one row of the position is left."""
        rivals = []

        def rival():
            rivals.append(UserSettings.objects.create(user=self.owner, key=POS_KEY, value=''))

        response, events = self._open(rival_on_lock=rival)
        self.assertEqual(len(rivals), 1, msg='fixture: the rival position must have been created')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(events.count('insert-position'), 1, msg='only the rival inserts the position')
        self.assertEqual(UserSettings.objects.filter(user=self.owner, key=POS_KEY).count(), 1,
                         msg='one row of the position must be left')
        body = response.content.decode('utf-8', 'replace')
        for title in ('TITLEAAA', 'TITLEBBB'):
            self.assertIn(title, body, msg='the response lists the widgets it created')
        # os widgets da resposta são os que a posição guarda (a última abertura vence)
        position = UserSettings.objects.get(user=self.owner, key=POS_KEY).value
        self.assertEqual(sorted(position.replace('|', ',').split(',')),
                         sorted(str(pk) for pk in UserWidget.objects.filter(user=self.owner).values_list('pk', flat=True)),
                         msg='the position must list the two widgets of this opening')

    def test_the_next_opening_lists_the_same_widgets_the_last_response_showed(self):
        """AC-9083-7: reinforcement (the real race is the manual evidence on the MySQL); the second opening lists the widgets of the first and creates no row."""
        first, _ = self._open()
        self.assertEqual(first.status_code, 200)
        # controle positivo: a 1ª abertura criou os dois widgets e a posição
        widget_ids = sorted(UserWidget.objects.filter(user=self.owner).values_list('pk', flat=True))
        self.assertEqual(len(widget_ids), 2, msg='fixture: the first opening creates both widgets')
        position_rows = UserSettings.objects.filter(user=self.owner, key=POS_KEY)
        self.assertEqual(position_rows.count(), 1, msg='fixture: the first opening creates the position')
        stored = position_rows.get().value
        second, _ = self._open()
        self.assertEqual(second.status_code, 200)
        shown_first, shown_second = (page.content.decode('utf-8', 'replace') for page in (first, second))
        for title in ('TITLEAAA', 'TITLEBBB'):
            self.assertIn(title, shown_first, msg='the response of the first opening lists the widgets it created')
            self.assertIn(title, shown_second, msg='the next opening must list the same widgets')
        self.assertEqual(sorted(UserWidget.objects.filter(user=self.owner).values_list('pk', flat=True)), widget_ids,
                         msg='the next opening must not create other widgets')
        self.assertEqual(UserSettings.objects.filter(user=self.owner, key=POS_KEY).count(), 1,
                         msg='the next opening must keep one row of the position')
        self.assertEqual(UserSettings.objects.get(user=self.owner, key=POS_KEY).value, stored,
                         msg='the next opening must not change the position the first one stored')
