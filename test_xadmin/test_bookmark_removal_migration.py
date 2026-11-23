# coding=utf-8
"""A migração que remove o model ``Bookmark`` derruba só a tabela do favorito. See #9054.

O pacote deixa de trazer o plugin e o model de favoritos; a migração ``0005_delete_bookmark`` (a folha depois da
``0004_normalize_action_flag``) é uma ``DeleteModel`` só, escrita à mão. Os testes partem do estado das migrações até a
``0004``, com a tabela ``xadmin_bookmark`` criada pelo model histórico e linhas semeadas, aplicam a folha e conferem:

* a tabela some (AC-9054-1) e a volta a recria vazia (AC-9054-3);
* as tabelas ``xadmin_usersettings``, ``xadmin_userwidget`` e ``xadmin_log`` não são tocadas (AC-9054-16): a introspecção
  do SQLite não vê um ``AlterField`` de ``id`` (``AutoField`` e ``BigAutoField`` geram a mesma coluna ``integer``,
  ``django/db/backends/sqlite3/base.py:67-68``), então a prova olha o DDL capturado durante o ``migrate`` e as operações
  da migração; colunas e linhas iguais antes e depois entram como complemento;
* o content type ``xadmin.bookmark`` e as permissões dele ficam (AC-9054-17).

O content type e as permissões do favorito são criados à mão no teste: num banco novo, sem o model, nenhum ``post_migrate``
os cria, e o ``flush`` que fecha cada ``TransactionTestCase`` não os devolve depois que o model sai.

O teste procura a folha do grafo em disco em vez de importar o nome da migração: sem a migração, o assert da folha falha
com a mensagem (e não um ``NodeNotFoundError``). É um ``TransactionTestCase`` (o editor de esquema do SQLite precisa dos
``foreign keys`` desligados, o que não consegue dentro do ``atomic`` de um ``TestCase``) e devolve as migrações à folha em
``addCleanup``, para um teste que falha no meio não deixar o seguinte com a tabela no estado antigo.

    python runtests.py test_xadmin.test_bookmark_removal_migration
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_bookmark_removal_migration'
"""
import re

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import Permission
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

APP = 'xadmin'
BEFORE = [(APP, '0004_normalize_action_flag')]
MIGRATION = '0005_delete_bookmark'
BOOKMARK_TABLE = 'xadmin_bookmark'
KEPT_TABLES = ('xadmin_usersettings', 'xadmin_userwidget', 'xadmin_log')
# as quatro permissões que o Django cria por model: o conjunto que o favorito deixa para trás
BOOKMARK_CODENAMES = ('add_bookmark', 'change_bookmark', 'delete_bookmark', 'view_bookmark')
# o que escreve ou muda o esquema: o resto (`PRAGMA`, `SELECT`) não toca tabela nenhuma
_WRITES = re.compile(r'^\s*(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE)\b', re.IGNORECASE)


class _MigrationCase(TransactionTestCase):
    """Um teste que começa com as migrações do xadmin até a ``0004`` e as devolve à folha no fim."""

    def setUp(self):
        super().setUp()
        # a restauração entra primeiro em `addCleanup`: vale também se o teste falhar no meio
        self.addCleanup(self._migrate_to_latest)
        self.old_apps = self._migrate(BEFORE)
        self.user = User.objects.create_user('bookmark-mig', 'bookmark-mig@example.com', 'not-a-real-password')
        self.content_type, _ = ContentType.objects.get_or_create(app_label=APP, model='bookmark')
        self.permissions = [
            Permission.objects.get_or_create(content_type=self.content_type, codename=codename,
                                             defaults={'name': 'Can %s bookmark' % codename.split('_')[0]})[0]
            for codename in BOOKMARK_CODENAMES]

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

    @staticmethod
    def _leaf():
        """A última migração do xadmin no grafo em disco."""
        return [node for node in MigrationExecutor(connection).loader.graph.leaf_nodes() if node[0] == APP]

    def _assert_the_leaf_is_the_removal(self):
        self.assertEqual(self._leaf(), [(APP, MIGRATION)],
                         msg='the migration that removes the Bookmark model must be the last one of the xadmin')

    def _tables(self):
        return connection.introspection.table_names()

    def _seed_bookmarks(self):
        Bookmark = self.old_apps.get_model(APP, 'Bookmark')
        Bookmark.objects.create(title='mine', user_id=self.user.pk, url_name='xadmin:index',
                                content_type_id=self.content_type.pk, query='a=1')
        Bookmark.objects.create(title='shared', user_id=None, url_name='xadmin:index',
                                content_type_id=self.content_type.pk, is_share=True)
        return Bookmark

    def _seed_kept_tables(self):
        """Linhas nas três tabelas que a migração não pode tocar, pelo model histórico."""
        self.old_apps.get_model(APP, 'UserSettings').objects.create(user_id=self.user.pk, key='k', value='v')
        self.old_apps.get_model(APP, 'UserWidget').objects.create(
            user_id=self.user.pk, page_id='home', widget_type='html', value='{}')
        self.old_apps.get_model(APP, 'Log').objects.create(
            user_id=self.user.pk, object_repr='obj', action_flag='create', message='m')

    def _snapshot(self, table):
        """(colunas, linhas) da tabela, para comparar antes e depois."""
        with connection.cursor() as cursor:
            columns = [(column.name, column.type_code) for column in
                       connection.introspection.get_table_description(cursor, table)]
            cursor.execute('SELECT * FROM %s ORDER BY 1' % connection.ops.quote_name(table))
            return columns, cursor.fetchall()


class RemoveBookmarkTests(_MigrationCase):
    """A ida e a volta da migração, e o que ela não toca."""

    def test_the_migration_drops_the_bookmark_table(self):
        """AC-9054-1: o `migrate` sobre um banco com a tabela do favorito (e linhas) remove a tabela."""
        Bookmark = self._seed_bookmarks()
        # pré-condição positiva: a tabela existe e tem as duas linhas antes
        self.assertIn(BOOKMARK_TABLE, self._tables(), msg='fixture: the table exists before the migration')
        self.assertEqual(Bookmark.objects.count(), 2, msg='fixture: the table has rows before the migration')
        self._assert_the_leaf_is_the_removal()
        self._migrate(self._leaf())
        self.assertNotIn(BOOKMARK_TABLE, self._tables(), msg='the migration must drop the bookmark table')

    def test_reversing_the_migration_recreates_the_empty_table(self):
        """AC-9054-3: desfazer a migração recria a tabela do favorito, vazia."""
        self._seed_bookmarks()
        self._assert_the_leaf_is_the_removal()
        self._migrate(self._leaf())
        self.assertNotIn(BOOKMARK_TABLE, self._tables(), msg='fixture: the table is gone before the reverse')
        old_apps = self._migrate(BEFORE)
        self.assertIn(BOOKMARK_TABLE, self._tables(), msg='the reverse must recreate the bookmark table')
        self.assertEqual(old_apps.get_model(APP, 'Bookmark').objects.count(), 0,
                         msg='the recreated table must be empty')

    def test_the_migration_does_not_touch_the_other_xadmin_tables(self):
        """AC-9054-16: o `migrate` não altera as colunas nem as linhas de `xadmin_usersettings`, `xadmin_userwidget` e `xadmin_log`."""
        self._seed_bookmarks()
        self._seed_kept_tables()
        before = {table: self._snapshot(table) for table in KEPT_TABLES}
        # pré-condição positiva: as três tabelas têm linha, então "linhas iguais" não é vazio
        for table, (columns, rows) in before.items():
            self.assertTrue(columns and rows, msg='fixture: %s has columns and a row' % table)
        self._assert_the_leaf_is_the_removal()
        # a migração ainda não existe no red: este é o único passo a aplicar, então a captura é o `migrate` inteiro
        executor = MigrationExecutor(connection)
        operations = executor.loader.get_migration(APP, MIGRATION).operations
        self.assertEqual([type(operation) for operation in operations], [migrations.DeleteModel],
                         msg='the migration must be exactly one DeleteModel (no AlterField of id, no RunPython)')
        with CaptureQueriesContext(connection) as captured:
            executor.migrate(self._leaf())
        statements = [query['sql'] for query in captured.captured_queries if _WRITES.match(query['sql'])]
        # pré-condição positiva: a captura viu o DDL da migração (ela derruba a tabela do favorito)
        self.assertTrue(any(BOOKMARK_TABLE in sql for sql in statements),
                        msg='fixture: the captured SQL must show the drop of the bookmark table')
        for table in KEPT_TABLES:
            with self.subTest(table=table):
                self.assertFalse([sql for sql in statements if table in sql],
                                 msg='the migration must not run DDL or writes on %s' % table)
                self.assertEqual(self._snapshot(table), before[table],
                                 msg='the columns and the rows of %s must be as they were' % table)

    def test_the_migration_keeps_the_content_type_and_the_permissions_of_the_bookmark(self):
        """AC-9054-17: o `migrate` não apaga o content type `xadmin.bookmark` nem as permissões dele."""
        # pré-condição positiva: o content type e as quatro permissões existem antes
        self.assertEqual(Permission.objects.filter(content_type=self.content_type).count(), len(BOOKMARK_CODENAMES),
                         msg='fixture: the permissions of the bookmark exist before the migration')
        self._assert_the_leaf_is_the_removal()
        self._migrate(self._leaf())
        self.assertTrue(ContentType.objects.filter(pk=self.content_type.pk, app_label=APP, model='bookmark').exists(),
                        msg='the content type of the bookmark must stay')
        self.assertEqual(sorted(Permission.objects.filter(content_type=self.content_type)
                                .values_list('codename', flat=True)),
                         sorted(BOOKMARK_CODENAMES), msg='the permissions of the bookmark must stay')
