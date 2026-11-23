# coding=utf-8
"""Nothing keeps ``UserSettings`` unique per (user, key): with two rows the last one written (the greatest id) wins. See #8983.

The table has no unique constraint, so a user can end up with two rows of the same key (two requests that
both found none and both created one). The readers behaved in three different ways when that happened:
the widget screen raised (a 500, and the widget was not even deleted), the theme, the form layout, the new
widget's position and the panel's delete swallowed the error and went on as if there was no setting at all,
and the panel page picked a row with no order defined. They all use the row with the greatest id now, and
the edits of a single row (a new widget, a deleted widget) change that same row.

The module pins the rule at every reader (AC-8983-1 to AC-8983-5) and pins what must stay as it was: the
behaviour with one row or none, and that a row of another user, or of another key of the same user, is
never read nor changed (AC-8983-6). Each scenario builds the older row first (the smaller id) with the "old"
value and the newer row after it, so reading the wrong row shows up in the result; a row of an intruder is
created *after* the user's own ones (a greater id) because, being the greatest id, it would win if the
reader dropped the user or the key.

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
        """The owner's rows of the key; the first one created has the smaller id."""
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

    # -- the readers, one method each ------------------------------------------------------------------

    def _theme_point(self, n, intruders, strict):
        self._fresh()
        self._rows(THEME_KEY, {0: [], 1: ['/static/solo.css'], 2: ['/static/old.css', '/static/new.css']}[n])
        extra = self._intruders(THEME_KEY, '/static/INTRUDER.css') if intruders else []
        view = SimpleNamespace(admin_site=None, request=SimpleNamespace(COOKIES={'_theme': '/static/cookie.css'}),
                               user=self.owner, args=(), kwargs={})
        got = ThemePlugin(view)._get_theme()
        self.assertNotEqual(got, '/static/INTRUDER.css', msg='the theme must not come from another user or key')
        if strict or n < 2:
            expected = {0: '/static/cookie.css', 1: '/static/solo.css', 2: '/static/new.css'}[n]
            self.assertEqual(got, expected, msg=f'theme with {n} row(s)')
        self._assert_untouched(extra, '/static/INTRUDER.css', 'theme')
        if n == 0:
            # sem linha e sem cookie vale o tema padrão do plugin (themes.py, o fim do `_get_theme`)
            bare = SimpleNamespace(admin_site=None, request=SimpleNamespace(COOKIES={}), user=self.owner,
                                   args=(), kwargs={})
            plugin = ThemePlugin(bare)
            self.assertEqual(plugin._get_theme(), plugin.default_theme,
                             msg='with no row and no cookie the default theme must apply')

    def _layout_point(self, n, intruders, strict, detail):
        self._fresh()
        key = DETAIL_KEY if detail else EDIT_KEY
        self._rows(key, {0: [], 1: ['box-1,box-0|'], 2: ['box-0,box-1|', 'box-1,box-0|']}[n])
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
        if strict or n < 2:
            expected = {0: [['box-0', 'box-1'], []], 1: [['box-1', 'box-0'], []], 2: [['box-1', 'box-0'], []]}[n]
            self.assertEqual(got, expected, msg=f'layout ({key}) with {n} row(s)')
        self._assert_untouched(extra, 'box-0|box-1', 'layout')

    def _panel_read_point(self, n, intruders, strict):
        self._fresh()
        widget_a, widget_b = self._widget('TITLEAAA'), self._widget('TITLEBBB')
        # com 2 linhas: no cenário estrito a antiga lista o B e a nova o A; no de isolamento as duas listam o A
        # (a linha usada não é o que ele confere) e quem lista o B é o intruso
        old = str(widget_b.pk) if strict else str(widget_a.pk)
        self._rows(POS_KEY, {0: [], 1: [str(widget_a.pk)], 2: [old, str(widget_a.pk)]}[n])
        extra = self._intruders(POS_KEY, str(widget_b.pk)) if intruders else []
        response = self._browser().get(PANEL_URL)
        body = response.content.decode('utf-8', 'replace')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('TITLEBBB', body, msg='the panel must not show the widgets of an old row or of another user/key')
        if strict or n < 2:
            self.assertEqual('TITLEAAA' in body, n >= 1, msg=f'panel with {n} row(s)')
        expected_rows = {0: 1, 1: 1, 2: 2}[n]  # sem linha, a 1ª leitura cria a posição padrão (uma só)
        self.assertEqual(len(self._values(POS_KEY)), expected_rows, msg=f'rows of the position after the page, n={n}')
        self._assert_untouched(extra, str(widget_b.pk), 'panel')

    def _create_point(self, n, intruders, strict):
        self._fresh()
        self._rows(POS_KEY, {0: [], 1: ['9,8'], 2: ['9,8', '7,6']}[n])
        extra = self._intruders(POS_KEY, '5,4') if intruders else []
        widget = UserWidget(user=self.owner, page_id='home', widget_type='html',
                            value=json.dumps({'title': 'NEW', 'content': 'new'}))
        widget.save()
        if strict or n < 2:
            expected = {0: [], 1: [f'{widget.pk},9,8'], 2: ['9,8', f'{widget.pk},7,6']}[n]
            self.assertEqual(self._values(POS_KEY), expected, msg=f'position after a new widget, n={n}')
        self._assert_untouched(extra, '5,4', 'new widget')

    def _delete_point(self, n, intruders, strict, how):
        self._fresh()
        widget, kept = self._widget('DELETED'), self._widget('KEPT')
        self._rows(POS_KEY, {0: [], 1: [f'{kept.pk},{widget.pk}'],
                             2: [f'{widget.pk},{kept.pk}', f'{kept.pk},{widget.pk}']}[n])
        extra = self._intruders(POS_KEY, f'{widget.pk},{kept.pk}') if intruders else []
        browser = self._browser()
        if how == 'screen':
            response = browser.post(f'{WIDGETS_URL}{widget.pk}/delete/', {})
        elif how == 'batch':
            response = browser.post(WIDGETS_URL, {'action': 'delete_selected', 'post': 'yes',
                                                  '_selected_action': [str(widget.pk)]})
        else:
            response = browser.post(PANEL_URL, {'id': str(widget.pk), '_delete': 'on'})
        if strict or n < 2:
            self.assertEqual(response.status_code, 200 if how == 'panel' else 302, msg=f'{how} delete, n={n}')
            self.assertFalse(UserWidget.objects.filter(pk=widget.pk).exists(), msg=f'{how} delete must delete the widget')
            if not (how == 'panel' and n == 0):  # o painel, sem linha, recria a posição padrão ao responder
                expected = {0: [], 1: [str(kept.pk)], 2: [f'{widget.pk},{kept.pk}', str(kept.pk)]}[n]
                self.assertEqual(self._values(POS_KEY), expected, msg=f'position after a {how} delete, n={n}')
        self._assert_untouched(extra, f'{widget.pk},{kept.pk}', f'{how} delete')

    def _every_point(self):
        """``{name: callable(n, intruders, strict)}`` for the readers and editors the criteria list."""
        return {
            'theme': self._theme_point,
            'edit layout': lambda n, i, s: self._layout_point(n, i, s, detail=False),
            'detail layout': lambda n, i, s: self._layout_point(n, i, s, detail=True),
            'panel page': self._panel_read_point,
            'new widget': self._create_point,
            'widget screen delete': lambda n, i, s: self._delete_point(n, i, s, 'screen'),
            'widget screen batch delete': lambda n, i, s: self._delete_point(n, i, s, 'batch'),
            'panel delete': lambda n, i, s: self._delete_point(n, i, s, 'panel'),
        }


class LatestRowWinsTests(_SettingsTestCase):
    """AC-8983-1 to AC-8983-5: with two rows of the same key the one with the greatest id is read and edited."""

    def test_panel_page_shows_the_position_of_the_latest_row(self):
        """AC-8983-1: the panel shows the widgets of the row with the greatest id, not of the older one."""
        self._panel_read_point(2, intruders=False, strict=True)

    def test_theme_and_form_layout_use_the_latest_row(self):
        """AC-8983-2: the theme and the edit and detail layouts come from the row with the greatest id."""
        with self.subTest(reader='theme'):
            self._theme_point(2, intruders=False, strict=True)
        for detail in (False, True):
            with self.subTest(reader='detail layout' if detail else 'edit layout'):
                self._layout_point(2, intruders=False, strict=True, detail=detail)

    def test_new_widget_goes_in_front_of_the_latest_row(self):
        """AC-8983-3: the id of a new widget goes in front of the row with the greatest id; the older row stays."""
        self._create_point(2, intruders=False, strict=True)

    def test_widget_screen_delete_edits_the_latest_row(self):
        """AC-8983-4: deleting a widget in the widget screen, one by one or in batch, edits the greatest id row."""
        for how in ('screen', 'batch'):
            with self.subTest(how=how):
                self._delete_point(2, intruders=False, strict=True, how=how)

    def test_widget_screen_batch_delete_of_two_widgets_edits_the_latest_row(self):
        """AC-8983-4: two widgets deleted in one batch leave the greatest id row without both ids; the older stays."""
        self._fresh()
        first, second, kept = self._widget('FIRST'), self._widget('SECOND'), self._widget('KEPT')
        old, new = f'{first.pk},{second.pk},{kept.pk}', f'{kept.pk},{first.pk},{second.pk}'
        self._rows(POS_KEY, [old, new])
        response = self._browser().post(WIDGETS_URL, {'action': 'delete_selected', 'post': 'yes',
                                                      '_selected_action': [str(first.pk), str(second.pk)]})
        self.assertEqual(response.status_code, 302, msg='the batch delete must redirect')
        self.assertEqual(list(UserWidget.objects.values_list('pk', flat=True)), [kept.pk],
                         msg='both widgets must be deleted and the third one kept')
        self.assertEqual(self._values(POS_KEY), [old, str(kept.pk)],
                         msg='both ids must leave the greatest id row; the older row stays as it was')

    def test_widget_screen_delete_by_another_user_edits_the_rows_of_the_widget_owner(self):
        """AC-8983-4: a superuser deleting a widget of someone else edits the greatest id row of the widget's owner."""
        for how in ('screen', 'batch'):
            with self.subTest(how=how):
                self._fresh()
                widget, kept = self._widget('DELETED'), self._widget('KEPT')  # os dois são do `owner`
                old, new = f'{widget.pk},{kept.pk}', f'{kept.pk},{widget.pk}'
                self._rows(POS_KEY, [old, new])
                # a linha do outro usuário, que apaga, é a de maior id da chave: ganharia se o código usasse o usuário errado
                mine = UserSettings.objects.create(user=self.other, key=POS_KEY, value=f'{widget.pk},{kept.pk}')
                browser = self._browser(self.other)
                if how == 'screen':
                    response = browser.post(f'{WIDGETS_URL}{widget.pk}/delete/', {})
                else:
                    response = browser.post(WIDGETS_URL, {'action': 'delete_selected', 'post': 'yes',
                                                          '_selected_action': [str(widget.pk)]})
                self.assertEqual(response.status_code, 302, msg=f'{how} delete by another user')
                self.assertFalse(UserWidget.objects.filter(pk=widget.pk).exists())
                self.assertEqual(self._values(POS_KEY), [old, str(kept.pk)],
                                 msg="the id must leave the greatest id row of the widget's owner")
                mine.refresh_from_db()
                self.assertEqual(mine.value, f'{widget.pk},{kept.pk}', msg='the row of the user who deletes stays')

    def test_panel_delete_edits_the_latest_row(self):
        """AC-8983-5: deleting a widget through the panel takes its id out of the row with the greatest id."""
        self._delete_point(2, intruders=False, strict=True, how='panel')


class OneRowOrNoneAndIsolationTests(_SettingsTestCase):
    """AC-8983-6: nothing changes with one row or none, and other users and other keys are left alone."""

    def test_zero_or_one_row_keeps_working_at_every_point(self):
        """AC-8983-6: with one row or none every reader and editor behaves as before, intruders or not."""
        points = self._every_point()
        self.assertTrue(points)
        for name, point in points.items():
            for n in (0, 1):
                for intruders in (False, True):
                    with self.subTest(point=name, rows=n, intruders=intruders):
                        point(n, intruders, True)

    def test_two_rows_never_read_or_change_a_row_of_another_user_or_key(self):
        """AC-8983-6: with two rows of their own, a row of another user or key (a greater id) is never read nor changed."""
        points = self._every_point()
        self.assertTrue(points)
        for name, point in points.items():
            with self.subTest(point=name):
                point(2, True, False)
