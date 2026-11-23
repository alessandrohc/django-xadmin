# coding=utf-8
"""The readers and editors of ``UserSettings`` keep working with one row or none, and with repeated rows (the greatest id wins). See #8983, #8982, #9083.

The #9083 takes the unique constraint of the #8982 out (the old databases cannot be asked to run a migration), so a key
can have more than one row again and the tolerant readers of the #8983 are the permanent defence: the greatest id is
read (AC-9083-3), all the rows are updated by the ``settings/user`` route (AC-9083-4), and a widget that is created or
removed edits the row of the greatest id (AC-9083-10). The tests of the repeated rows build them with a plain insert,
which fails the fixture while the table has the constraint.


The #8983 made every reader of ``UserSettings`` tolerate a key with more than one row (the greatest id wins). What this
module also pins is what must stay as it was, at every point the #8983 touched: the behaviour with one row or none
(AC-8982-10), the first opening of the
panel and the new widget (AC-8982-11), the delete of a widget (AC-8982-12), and that a row of another user, or of
another key of the same user, is never read nor changed (AC-8982-13). A row of an intruder is created *after* the
user's own ones (a greater id) because, being the greatest id, it would win if the reader dropped the user or the key.

The theme and the layout are read by plugins that only exist when the package runs alone (the host excludes
``themes`` and ``portal`` in ``XADMIN_EXCLUDE_PLUGINS``), so they are called directly, as plugins, with a
stand-in view; the panel, the widget screen and the delete go through the test client. The client is built
with ``raise_request_exception=False`` so a 500 shows up as a status. The attribute ``client`` of the
``TestCase`` is left alone: the user's client is built per call.

    python runtests.py test_xadmin.test_user_settings_latest
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_user_settings_latest'
"""
import json
from types import SimpleNamespace

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import Client, TestCase

from xadmin.layout import Column, Fieldset
from xadmin.models import UserSettings, UserWidget
from xadmin.plugins.portal import ModelDetailPlugin, ModelFormPlugin
from xadmin.plugins.themes import ThemePlugin

from test_xadmin.fixtureapp.models import Author

POS_KEY = 'dashboard:home:pos'
THEME_KEY = 'site-theme'
EDIT_KEY = 'xadmin_fixture_author_editform_portal'
DETAIL_KEY = 'xadmin_fixture_author_detail_portal'
UNRELATED_KEY = 'unrelated-setting'

PANEL_URL = '/xadmin/'
WIDGETS_URL = '/xadmin/xadmin/userwidget/'
SETTINGS_URL = '/xadmin/settings/user'


def _superuser(username):
    return User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password',
                                    is_staff=True, is_superuser=True)


class _SettingsTestCase(TestCase):
    """The two users, the rows and the readers the scenarios share."""

    def setUp(self):
        self.owner = _superuser('owner')
        self.other = _superuser('other')

    def _fresh(self):
        # cada cenário começa do zero: o TestCase só desfaz as linhas no fim do método, e os subtestes dividem o banco
        UserSettings.objects.all().delete()
        UserWidget.objects.all().delete()

    def _browser(self, user=None):
        client = Client(raise_request_exception=False)
        client.force_login(user or self.owner)
        return client

    def _widget(self, title):
        return UserWidget.objects.create(user=self.owner, page_id='home', widget_type='html',
                                         value=json.dumps({'title': title, 'content': title.lower()}))

    def _rows(self, key, values):
        """The owner's rows of the key (none or one: the table refuses more)."""
        return [UserSettings.objects.create(user=self.owner, key=key, value=value) for value in values]

    def _intruders(self, key, value):
        """A row of another user with the same key and a row of the owner with another key, both with a greater id."""
        return [UserSettings.objects.create(user=self.other, key=key, value=value),
                UserSettings.objects.create(user=self.owner, key=UNRELATED_KEY, value=value)]

    def _values(self, key):
        return list(UserSettings.objects.filter(user=self.owner, key=key)
                    .order_by('pk').values_list('value', flat=True))

    def _assert_untouched(self, intruders, value, point):
        for row in intruders:
            row.refresh_from_db()
            self.assertEqual(row.value, value,
                             msg=f'{point}: the row {row.key!r} of {row.user.username} must not be changed')

    # -- the readers and editors, one method each ------------------------------------------------------

    def _theme_point(self, n, intruders):
        self._fresh()
        self._rows(THEME_KEY, {0: [], 1: ['/static/solo.css']}[n])
        extra = self._intruders(THEME_KEY, '/static/INTRUDER.css') if intruders else []
        view = SimpleNamespace(admin_site=None, request=SimpleNamespace(COOKIES={'_theme': '/static/cookie.css'}),
                               user=self.owner, args=(), kwargs={})
        got = ThemePlugin(view)._get_theme()
        self.assertNotEqual(got, '/static/INTRUDER.css', msg='the theme must not come from another user or key')
        expected = {0: '/static/cookie.css', 1: '/static/solo.css'}[n]
        self.assertEqual(got, expected, msg=f'theme with {n} row(s)')
        self._assert_untouched(extra, '/static/INTRUDER.css', 'theme')
        if n == 0:
            # sem linha e sem cookie vale o tema padrão do plugin (themes.py, o fim do `_get_theme`)
            bare = SimpleNamespace(admin_site=None, request=SimpleNamespace(COOKIES={}), user=self.owner,
                                   args=(), kwargs={})
            plugin = ThemePlugin(bare)
            self.assertEqual(plugin._get_theme(), plugin.default_theme,
                             msg='with no row and no cookie the default theme must apply')

    def _layout_point(self, n, intruders, detail):
        self._fresh()
        key = DETAIL_KEY if detail else EDIT_KEY
        self._rows(key, {0: [], 1: ['box-1,box-0|']}[n])
        extra = self._intruders(key, 'box-0|box-1') if intruders else []
        helper = FormHelper()
        helper.layout = Layout(
            Column(Fieldset('A', 'name', css_id='box-0'), Fieldset('B', css_id='box-1'), css_id='column-0'),
            Column(css_id='column-1'))
        view = SimpleNamespace(admin_site=None, request=SimpleNamespace(COOKIES={}), user=self.owner,
                               args=(), kwargs={}, model=Author)
        plugin = (ModelDetailPlugin if detail else ModelFormPlugin)(view)
        got = [[field.css_id for field in column.fields] for column in plugin.get_form_helper(helper).layout.fields]
        self.assertNotEqual(got, [['box-0'], ['box-1']], msg='the layout must not come from another user or key')
        expected = {0: [['box-0', 'box-1'], []], 1: [['box-1', 'box-0'], []]}[n]
        self.assertEqual(got, expected, msg=f'layout ({key}) with {n} row(s)')
        self._assert_untouched(extra, 'box-0|box-1', 'layout')

    def _panel_read_point(self, n, intruders):
        self._fresh()
        widget_a, widget_b = self._widget('TITLEAAA'), self._widget('TITLEBBB')
        self._rows(POS_KEY, {0: [], 1: [str(widget_a.pk)]}[n])
        # o widget B só aparece se o painel ler a linha do intruso
        extra = self._intruders(POS_KEY, str(widget_b.pk)) if intruders else []
        response = self._browser().get(PANEL_URL)
        body = response.content.decode('utf-8', 'replace')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('TITLEBBB', body, msg='the panel must not show the widgets of another user or key')
        self.assertEqual('TITLEAAA' in body, n == 1, msg=f'panel with {n} row(s)')
        # sem linha, a 1ª leitura cria a posição padrão (uma só); com uma, não cria outra
        self.assertEqual(len(self._values(POS_KEY)), 1, msg=f'rows of the position after the page, n={n}')
        self._assert_untouched(extra, str(widget_b.pk), 'panel')

    def _create_point(self, n, intruders):
        self._fresh()
        self._rows(POS_KEY, {0: [], 1: ['9,8']}[n])
        extra = self._intruders(POS_KEY, '5,4') if intruders else []
        widget = UserWidget(user=self.owner, page_id='home', widget_type='html',
                            value=json.dumps({'title': 'NEW', 'content': 'new'}))
        widget.save()
        expected = {0: [], 1: [f'{widget.pk},9,8']}[n]
        self.assertEqual(self._values(POS_KEY), expected, msg=f'position after a new widget, n={n}')
        self._assert_untouched(extra, '5,4', 'new widget')

    def _delete_point(self, n, intruders, how):
        self._fresh()
        widget, kept = self._widget('DELETED'), self._widget('KEPT')
        self._rows(POS_KEY, {0: [], 1: [f'{kept.pk},{widget.pk}']}[n])
        extra = self._intruders(POS_KEY, f'{widget.pk},{kept.pk}') if intruders else []
        browser = self._browser()
        if how == 'screen':
            response = browser.post(f'{WIDGETS_URL}{widget.pk}/delete/', {})
        elif how == 'batch':
            response = browser.post(WIDGETS_URL, {'action': 'delete_selected', 'post': 'yes',
                                                  '_selected_action': [str(widget.pk)]})
        else:
            response = browser.post(PANEL_URL, {'id': str(widget.pk), '_delete': 'on'})
        self.assertEqual(response.status_code, 200 if how == 'panel' else 302, msg=f'{how} delete, n={n}')
        self.assertFalse(UserWidget.objects.filter(pk=widget.pk).exists(), msg=f'{how} delete must delete the widget')
        if not (how == 'panel' and n == 0):  # o painel, sem linha, recria a posição padrão ao responder
            expected = {0: [], 1: [str(kept.pk)]}[n]
            self.assertEqual(self._values(POS_KEY), expected, msg=f'position after a {how} delete, n={n}')
        self._assert_untouched(extra, f'{widget.pk},{kept.pk}', f'{how} delete')

    def _read_points(self):
        """``{name: callable(n, intruders)}`` for the readers the criteria list."""
        return {
            'theme': self._theme_point,
            'edit layout': lambda n, i: self._layout_point(n, i, detail=False),
            'detail layout': lambda n, i: self._layout_point(n, i, detail=True),
            'panel page': self._panel_read_point,
        }

    def _edit_points(self):
        """``{name: callable(n, intruders)}`` for the editors the criteria list."""
        return {
            'new widget': self._create_point,
            'widget screen delete': lambda n, i: self._delete_point(n, i, 'screen'),
            'widget screen batch delete': lambda n, i: self._delete_point(n, i, 'batch'),
            'panel delete': lambda n, i: self._delete_point(n, i, 'panel'),
        }


class OneRowOrNoneTests(_SettingsTestCase):
    """AC-8982-10, AC-8982-11, AC-8982-12: nothing changes with one row or none."""

    def test_zero_or_one_row_reads_as_before(self):
        """AC-8982-10: with one row the theme, the layouts and the panel use its value; with none, the fallback."""
        points = self._read_points()
        self.assertTrue(points)
        for name, point in points.items():
            for n in (0, 1):
                with self.subTest(point=name, rows=n):
                    point(n, False)

    def test_first_opening_of_the_panel_and_a_new_widget_keep_the_position(self):
        """AC-8982-11: the first opening creates one default row; a new widget goes in front of the only row."""
        with self.subTest(point='first opening'):
            self._panel_read_point(0, False)
        for n in (0, 1):
            with self.subTest(point='new widget', rows=n):
                self._create_point(n, False)

    def test_deleting_a_widget_takes_its_id_out_of_the_only_row(self):
        """AC-8982-12: the widget screen (one by one or in batch) and the panel delete the widget and edit the row."""
        for how in ('screen', 'batch', 'panel'):
            for n in (0, 1):
                with self.subTest(how=how, rows=n):
                    self._delete_point(n, False, how)

    def test_widget_screen_batch_delete_of_two_widgets_leaves_the_only_row_without_both_ids(self):
        """AC-8982-12: two widgets deleted in one batch leave the only row without both ids."""
        self._fresh()
        first, second, kept = self._widget('FIRST'), self._widget('SECOND'), self._widget('KEPT')
        self._rows(POS_KEY, [f'{first.pk},{second.pk},{kept.pk}'])
        response = self._browser().post(WIDGETS_URL, {'action': 'delete_selected', 'post': 'yes',
                                                      '_selected_action': [str(first.pk), str(second.pk)]})
        self.assertEqual(response.status_code, 302, msg='the batch delete must redirect')
        self.assertEqual(list(UserWidget.objects.values_list('pk', flat=True)), [kept.pk],
                         msg='both widgets must be deleted and the third one kept')
        self.assertEqual(self._values(POS_KEY), [str(kept.pk)], msg='both ids must leave the only row')

    def test_widget_screen_delete_by_another_user_edits_the_row_of_the_widget_owner(self):
        """AC-8982-12: a superuser deleting a widget of someone else edits the only row of the widget's owner."""
        for how in ('screen', 'batch'):
            with self.subTest(how=how):
                self._fresh()
                widget, kept = self._widget('DELETED'), self._widget('KEPT')  # os dois são do `owner`
                self._rows(POS_KEY, [f'{widget.pk},{kept.pk}'])
                # a linha de quem apaga, de maior id que a do dono: ganharia se o código usasse o usuário errado
                mine = UserSettings.objects.create(user=self.other, key=POS_KEY, value=f'{widget.pk},{kept.pk}')
                browser = self._browser(self.other)
                if how == 'screen':
                    response = browser.post(f'{WIDGETS_URL}{widget.pk}/delete/', {})
                else:
                    response = browser.post(WIDGETS_URL, {'action': 'delete_selected', 'post': 'yes',
                                                          '_selected_action': [str(widget.pk)]})
                self.assertEqual(response.status_code, 302, msg=f'{how} delete by another user')
                self.assertFalse(UserWidget.objects.filter(pk=widget.pk).exists())
                self.assertEqual(self._values(POS_KEY), [str(kept.pk)],
                                 msg="the id must leave the row of the widget's owner")
                mine.refresh_from_db()
                self.assertEqual(mine.value, f'{widget.pk},{kept.pk}', msg='the row of the user who deletes stays')


class IsolationTests(_SettingsTestCase):
    """AC-8982-13: a row of another user or of another key (a greater id) is never read nor changed."""

    def test_the_readers_never_read_or_change_a_row_of_another_user_or_key(self):
        """AC-8982-13: the theme, the layouts and the panel read only the row of the user and the key."""
        points = self._read_points()
        self.assertTrue(points)
        for name, point in points.items():
            for n in (0, 1):
                with self.subTest(point=name, rows=n):
                    point(n, True)

    def test_the_editors_never_read_or_change_a_row_of_another_user_or_key(self):
        """AC-8982-13: a new widget and the deletes change only the row of the widget's owner and the key."""
        points = self._edit_points()
        self.assertTrue(points)
        for name, point in points.items():
            for n in (0, 1):
                with self.subTest(point=name, rows=n):
                    point(n, True)


class RepeatedRowsTests(_SettingsTestCase):
    """AC-9083-3, AC-9083-4, AC-9083-10: a key with more than one row of the same user."""

    def _repeat(self, key, values):
        """The owner's rows of the key, oldest first (the last one has the greatest id). Fails while the table refuses."""
        rows = []
        for value in values:
            try:
                with transaction.atomic():
                    rows.append(UserSettings.objects.create(user=self.owner, key=key, value=value))
            except IntegrityError:
                self.fail('fixture: two rows of the same user and key cannot be built, the table still has the unique '
                          'constraint (the #9083 takes it out)')
        return rows

    def test_the_theme_and_the_panel_position_read_the_row_of_the_greatest_id(self):
        """AC-9083-3: with two rows of the key the theme and the panel use the value of the greater id."""
        with self.subTest(point='theme'):
            self._fresh()
            self._repeat(THEME_KEY, ['/static/old.css', '/static/new.css'])
            view = SimpleNamespace(admin_site=None, request=SimpleNamespace(COOKIES={'_theme': '/static/cookie.css'}),
                                   user=self.owner, args=(), kwargs={})
            self.assertEqual(ThemePlugin(view)._get_theme(), '/static/new.css',
                             msg='the theme must be the value of the row of the greatest id')
        with self.subTest(point='panel position'):
            self._fresh()
            widget_a, widget_b = self._widget('TITLEAAA'), self._widget('TITLEBBB')
            self._repeat(POS_KEY, [str(widget_a.pk), str(widget_b.pk)])
            response = self._browser().get(PANEL_URL)
            body = response.content.decode('utf-8', 'replace')
            self.assertEqual(response.status_code, 200)
            self.assertIn('TITLEBBB', body, msg='the panel must list the widgets of the row of the greatest id')
            self.assertNotIn('TITLEAAA', body, msg='the panel must not list the widgets of the older row')

    def test_saving_a_key_with_two_rows_updates_all_of_them(self):
        """AC-9083-4: ``settings/user`` updates every row of the key and answers 200 with an empty body."""
        old, new = self._repeat('legacy-key', ['old', 'new'])
        intruder = UserSettings.objects.create(user=self.other, key='legacy-key', value='keep')
        response = self._browser().post(SETTINGS_URL, {'key': 'legacy-key', 'value': 'saved'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        old.refresh_from_db()
        new.refresh_from_db()
        intruder.refresh_from_db()
        self.assertEqual((old.value, new.value), ('saved', 'saved'), msg='both rows of the key must be updated')
        self.assertEqual(intruder.value, 'keep', msg='the row of another user must not change')

    def test_a_new_widget_and_a_removed_one_edit_the_row_of_the_greatest_id(self):
        """AC-9083-10: with two rows of the position the new widget and the removal change only the greater id."""
        with self.subTest(point='new widget'):
            self._fresh()
            old, new = self._repeat(POS_KEY, ['7,6', '9,8'])
            widget = UserWidget(user=self.owner, page_id='home', widget_type='html',
                                value=json.dumps({'title': 'NEW', 'content': 'new'}))
            widget.save()
            old.refresh_from_db()
            new.refresh_from_db()
            self.assertEqual(new.value, f'{widget.pk},9,8', msg='the id of the new widget goes in front of the greater row')
            self.assertEqual(old.value, '7,6', msg='the older row must not change')
        with self.subTest(point='removed widget'):
            self._fresh()
            widget, kept = self._widget('DELETED'), self._widget('KEPT')
            old, new = self._repeat(POS_KEY, [f'{kept.pk},{widget.pk}', f'{kept.pk},{widget.pk}'])
            response = self._browser().post(f'{WIDGETS_URL}{widget.pk}/delete/', {})
            self.assertEqual(response.status_code, 302)
            self.assertFalse(UserWidget.objects.filter(pk=widget.pk).exists())
            old.refresh_from_db()
            new.refresh_from_db()
            self.assertEqual(new.value, str(kept.pk), msg='the id of the removed widget leaves the greater row')
            self.assertEqual(old.value, f'{kept.pk},{widget.pk}', msg='the older row must not change')
