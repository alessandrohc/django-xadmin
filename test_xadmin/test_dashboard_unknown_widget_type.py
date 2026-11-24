# coding=utf-8
"""Saving a dashboard widget of a type the package does not offer answers the page, never 500. See #9088.

``Dashboard.post`` looks the saved widget up by user, page and id, and then asks the widget manager for the class of
its type. The types of the plugins that #9054 took out of the package (``bookmark``, ``chart``) are no longer
registered, so a row saved with one of them made that lookup raise ``KeyError``, and the client got a 500. Opening the
dashboard (GET) already copes with such a row: it drops the widget and answers the page (AC-9054-8, AC-9054-15). These
tests pin what the save (POST) answers now: the page of the same route the GET would answer, without the widget, and
nothing of what was sent stored in it.

The two dashboards of the package are covered: the one of the site (``/xadmin/``, which the host replaces by a welcome
page, so there it is the page of that route that is answered) and the one of an object (``<object_id>/dashboard/``,
the only widget panel the host shows). Each one with the widget on the saved position of the dashboard and off it,
because the clean-up of the GET only visits what the position lists.

The client is built with ``raise_request_exception=False`` so a 500 shows up as a status to compare, and the red
fails on the assertion instead of erroring on the exception. The saved title of an unknown widget is ``zz-target-title``,
the title of the widget that proves the page opened is ``zz-control-title``, and what the POST sends is
``zz-sent-title`` (a valid save of the control sends ``zz-valid-title``, so the two cannot be mistaken).

    python runtests.py test_xadmin.test_dashboard_unknown_widget_type
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_dashboard_unknown_widget_type'
"""
import json

from django.contrib.auth.models import User
from django.test import TestCase

from xadmin.models import UserSettings, UserWidget
from xadmin.views.dashboard import widget_manager

from test_xadmin.fixtureapp.models import Author
from test_xadmin.test_dashboard_widget_data import _ClientMixin, _widget

URL = '/xadmin/'
AUTHOR = 'xadmin_fixture.author'
PAGES = {
    'dashboard': 'home',
    'object': 'model:xadmin_fixture/author',
}
# `bookmark` e `chart` são os tipos dos módulos que o #9054 tirou do pacote; o terceiro nunca existiu
UNKNOWN_TYPES = (
    ('bookmark', {'bookmark': '1'}),
    ('chart', {'model': AUTHOR}),
    ('zz-unknown-type', {}),
)
# o painel do site e o do objeto, cada um com o widget na posição salva do painel e fora dela
CONTEXTS = (
    ('dashboard', True),
    ('dashboard', False),
    ('object', True),
    ('object', False),
)


class UnknownWidgetTypeTests(_ClientMixin, TestCase):
    """O POST de salvar um widget próprio, daquela página, de um tipo que o seletor de tipos do painel não oferece."""

    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser('uwt-root', 'uwt-root@example.com', 'not-a-real-password')
        cls.author = Author.objects.create(name='unknown-type-author')

    def _url(self, kind):
        if kind == 'dashboard':
            return URL
        return '/xadmin/xadmin_fixture/author/%d/dashboard/' % self.author.pk

    def _arrange(self, kind, in_pos, widget_type, value):
        """Um painel só com o widget de controle na posição e o alvo (de tipo desconhecido), na posição ou fora dela."""
        UserWidget.objects.all().delete()
        UserSettings.objects.all().delete()
        page_id = PAGES[kind]
        control = _widget(self.root, 'list', page_id, title='zz-control-title', model=AUTHOR)
        target = _widget(self.root, widget_type, page_id, title='zz-target-title', **value)
        listed = [control.pk] + ([target.pk] if in_pos else [])
        UserSettings.objects.create(user=self.root, key='dashboard:%s:pos' % page_id,
                                    value=','.join(str(pk) for pk in listed))
        return control, target

    def _assert_the_type_is_unknown(self, widget_type):
        """O tipo do alvo não é do seletor, e o `html` e o `list` (os de controle) são."""
        offered = {widget.widget_type for widget in widget_manager.get_widgets('home')}
        self.assertNotIn(widget_type, offered, msg='fixture: the type of the target is not offered')
        self.assertTrue({'html', 'list'} <= offered, msg='fixture: the types of the controls are offered')

    def _assert_a_valid_save_works(self, kind):
        """Controle positivo: na mesma rota, o POST do widget `html` válido grava e responde a página com o controle."""
        UserWidget.objects.all().delete()
        UserSettings.objects.all().delete()
        page_id = PAGES[kind]
        control = _widget(self.root, 'list', page_id, title='zz-control-title', model=AUTHOR)
        valid = _widget(self.root, 'html', page_id, title='before', content='<p>before</p>')
        UserSettings.objects.create(user=self.root, key='dashboard:%s:pos' % page_id, value='%d,%d' % (control.pk, valid.pk))
        response = self._client_for(self.root).post(
            self._url(kind), {'id': valid.pk, 'title': 'zz-valid-title', 'content': '<p>after</p>'})
        self.assertEqual(response.status_code, 200, msg='fixture: a valid save answers the page of the route (%s)' % kind)
        self.assertIn('zz-control-title', response.content.decode('utf-8'),
                      msg='fixture: the page of the route (%s) shows the control widget' % kind)
        self.assertEqual(json.loads(UserWidget.objects.get(pk=valid.pk).value)['title'], 'zz-valid-title',
                         msg='fixture: a valid save reaches the database (%s)' % kind)

    def test_saving_a_widget_of_an_unknown_type_answers_the_page_without_it(self):
        """AC-9088-1: salvar o widget próprio de um tipo que o seletor não oferece responde sem erro a página da rota, sem esse widget, no painel e no painel do objeto, com o widget na posição ou fora dela."""
        for kind in PAGES:
            self._assert_a_valid_save_works(kind)
        cases = 0
        for kind, in_pos in CONTEXTS:
            for widget_type, value in UNKNOWN_TYPES:
                with self.subTest(kind=kind, in_pos=in_pos, widget_type=widget_type):
                    cases += 1
                    self._assert_the_type_is_unknown(widget_type)
                    control, target = self._arrange(kind, in_pos, widget_type, value)
                    response = self._client_for(self.root).post(self._url(kind), {'id': target.pk, 'title': 'zz-sent-title'})
                    self.assertEqual(response.status_code, 200, msg='a widget of an unknown type must not make the save a 500')
                    body = response.content.decode('utf-8')
                    self.assertIn('zz-control-title', body, msg='the page of the route is answered (the control widget is on it)')
                    self.assertNotIn('zz-target-title', body, msg='the widget of the unknown type is not on the page')
                    self.assertNotIn('zz-sent-title', body, msg='what was sent is not on the page')
        self.assertEqual(cases, len(CONTEXTS) * len(UNKNOWN_TYPES), msg='the loop must run every case')

    def test_saving_a_widget_of_an_unknown_type_stores_nothing_of_what_was_sent(self):
        """AC-9088-2: salvar o widget próprio de um tipo que o seletor não oferece não grava os dados enviados: a linha que sobra tem o valor de antes, e a que sumiu não deixa o dado em linha nenhuma."""
        # o controle positivo é o da outra prova: o POST de um widget válido grava (AC-8930-11)
        for kind in PAGES:
            self._assert_a_valid_save_works(kind)
        cases = 0
        for kind, in_pos in CONTEXTS:
            for widget_type, value in UNKNOWN_TYPES:
                with self.subTest(kind=kind, in_pos=in_pos, widget_type=widget_type):
                    cases += 1
                    control, target = self._arrange(kind, in_pos, widget_type, value)
                    before = json.loads(target.value)
                    # `title` e `model` são campos dos widgets de lista e de gráfico: o dado enviado tenta entrar por qualquer um
                    self._client_for(self.root).post(self._url(kind), {'id': target.pk, 'title': 'zz-sent-title', 'model': AUTHOR})
                    row = UserWidget.objects.filter(pk=target.pk).first()
                    if row is not None:
                        self.assertEqual(json.loads(row.value), before, msg='the row that stays keeps the value it had')
                    stored = [pk for pk, saved in UserWidget.objects.values_list('pk', 'value') if 'zz-sent-title' in saved]
                    self.assertEqual(stored, [], msg='no row may hold what was sent')
        self.assertEqual(cases, len(CONTEXTS) * len(UNKNOWN_TYPES), msg='the loop must run every case')

    def test_deleting_a_widget_of_an_unknown_type_keeps_working(self):
        """AC-8930-12 (controle): excluir o widget próprio de um tipo que o seletor não oferece apaga a linha e responde o painel."""
        for widget_type, value in UNKNOWN_TYPES:
            with self.subTest(widget_type=widget_type):
                self._assert_the_type_is_unknown(widget_type)
                control, target = self._arrange('dashboard', True, widget_type, value)
                response = self._client_for(self.root).post(URL, {'id': target.pk, '_delete': 'on'})
                self.assertEqual(response.status_code, 200, msg='the delete of a widget of an unknown type answers the page')
                self.assertIn('zz-control-title', response.content.decode('utf-8'), msg='fixture: the page shows the control widget')
                self.assertFalse(UserWidget.objects.filter(pk=target.pk).exists(), msg='the row is deleted')
                self.assertTrue(UserWidget.objects.filter(pk=control.pk).exists(), msg='only that widget is deleted')

    def test_saving_an_own_widget_of_an_unknown_type_from_another_page_keeps_answering_404(self):
        """AC-8930-3 (controle): o widget próprio de tipo desconhecido que está em outra página segue com 404 e não grava: a página é conferida antes do tipo."""
        for widget_type, value in UNKNOWN_TYPES:
            with self.subTest(widget_type=widget_type):
                control, target = self._arrange('dashboard', True, widget_type, value)
                elsewhere = _widget(self.root, widget_type, 'zz-other-page', title='zz-elsewhere-title', **value)
                before = list(UserWidget.objects.order_by('pk').values_list('pk', 'page_id', 'value'))
                response = self._client_for(self.root).post(URL, {'id': elsewhere.pk, 'title': 'zz-sent-title'})
                self.assertEqual(response.status_code, 404, msg='a widget of another page is not a widget of this page')
                self.assertEqual(list(UserWidget.objects.order_by('pk').values_list('pk', 'page_id', 'value')), before,
                                 msg='nothing may be saved')
