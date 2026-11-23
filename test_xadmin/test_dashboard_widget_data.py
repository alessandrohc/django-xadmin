# coding=utf-8
"""A dashboard widget with data its type refuses answers 400 or shows the field error, never 500. See #8987.

``Dashboard.post`` builds the widget from the POST, and the form of the widget type refuses what is wrong.
That refusal was an uncaught ``WidgetDataError`` (the quick button without an ``url``) or, worse, an
``AttributeError``/``ValueError``/``LookupError`` from ``ModelChoiceField.to_python`` (the list, add-form and
chart widgets without a registered model), so the client got a 500. The same field made step 2 of the
"add widget" wizard answer 500. These tests pin what a client should get instead: 400 with no body and
nothing saved on the dashboard, the field error on the wizard, the 403 for a user without the permission,
and the clean-up of a saved widget whose model no longer exists when the dashboard is opened.

The client is built with ``raise_request_exception=False`` so a 500 shows up as a status to compare, and the
red fails on the assertion instead of erroring on the exception. The attribute of the ``TestCase`` is called
``client``, so the actors' client has another name.

Models used (``evidence/T-0.md``, M3): ``xadmin_fixture.author`` is registered in the xadmin site; ``contenttypes.
contenttype`` and ``xadmin_fixture.dummymodel`` exist and are NOT registered (``auth.permission`` IS registered, so
it cannot stand for "not registered"); ``nope.nope`` and ``xadmin_fixture.nonexistent`` do not exist.

    python runtests.py test_xadmin.test_dashboard_widget_data
    # in the host project's container:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_dashboard_widget_data'
"""
import json
import re
from unittest import mock

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.test import Client, SimpleTestCase, TestCase
from django.utils.html import escape

from xadmin.models import UserSettings, UserWidget
from xadmin.views import website
from xadmin.views.dashboard import ModelChoiceField, widget_manager

from test_xadmin.fixtureapp.models import Author

URL = '/xadmin/'
ADD_URL = '/xadmin/xadmin/userwidget/add/'
PARAMS_STEP = 'Widget Params'

AUTHOR = 'xadmin_fixture.author'
# o rótulo bem formado de um app ou de um modelo que não existe (o `LookupError` do `apps.get_model`)
GONE_MODELS = ('nope.nope', 'xadmin_fixture.nonexistent')
# existem no Django e não estão no site do xadmin: o `valid_value` do campo os recusa
UNREGISTERED_MODELS = ('contenttypes.contenttype', 'xadmin_fixture.dummymodel')
MODEL_TYPES = ('list', 'addform', 'chart')
# valores do campo de modelo que o campo recusa (além de ausente: o POST sem a chave)
BAD_MODEL_VALUES = ('', 'zzz', 'a.b.c') + GONE_MODELS + UNREGISTERED_MODELS

# os dados que o formulário do tipo recusa, por tipo de widget (AC-8987-1)
INVALID_DATA = {
    'qbutton': [{}, {'title': 'x'}, {'url': ''}, {'url': '   '}],
    'list': [{}] + [{'model': value} for value in BAD_MODEL_VALUES],
    'addform': [{}] + [{'model': value} for value in BAD_MODEL_VALUES],
    'chart': [{}] + [{'model': value} for value in BAD_MODEL_VALUES],
    'bookmark': [{'bookmark': '99999'}, {'bookmark': 'abc'}],
}


def _staff(username, *codenames):
    """Usuário da equipe (o xadmin só deixa a equipe entrar) com exatamente as permissões dadas."""
    user = User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password',
                                    is_staff=True)
    if codenames:
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def _widget(user, widget_type, page_id='home', **value):
    """Linha de `UserWidget` do usuário: o `post` monta o widget com o tipo da linha e os dados do POST."""
    return UserWidget.objects.create(user=user, page_id=page_id, widget_type=widget_type, value=json.dumps(value))


def _rows():
    return list(UserWidget.objects.order_by('pk').values_list('pk', 'user_id', 'widget_type', 'value'))


def _value(widget):
    return json.loads(UserWidget.objects.get(pk=widget.pk).value)


def _step_field(body):
    """O nome do campo de controle do assistente (`...-current_step`) na página."""
    match = re.search(r'name="([^"]*current_step)"', body)
    if match is None:
        raise AssertionError('the wizard control field is not on the page')
    return match.group(1)


def _current_step(body):
    """O valor do campo de controle do assistente na página, ou None."""
    match = re.search(r'name="[^"]*current_step"[^>]*value="([^"]*)"', body)
    return match.group(1) if match else None


class _ClientMixin:
    """Um cliente que vira o 500 em resposta, em vez de levantar a exceção."""

    def _client_for(self, user=None):
        client = Client(raise_request_exception=False)
        if user is not None:
            client.force_login(user)
        return client


def _open_dashboard(test, user, widget_type, value, url=URL, page_id='home'):
    """Abre o painel (GET) com o widget salvo `value` e um `list` válido de controle na posição do painel.

    Devolve (resposta, controle, alvo). O controle com título próprio prova que o painel abriu de verdade:
    sem ele, "o alvo não aparece" passaria com a página errada.
    """
    UserWidget.objects.all().delete()
    UserSettings.objects.all().delete()
    control = _widget(user, 'list', page_id, title='zz-control-title', model=AUTHOR)
    target = _widget(user, widget_type, page_id, title='zz-target-title', **value)
    UserSettings.objects.create(user=user, key='dashboard:%s:pos' % page_id,
                                value='%d,%d' % (control.pk, target.pk))
    return test._client_for(user).get(url), control, target


class SaveInvalidDataTests(_ClientMixin, TestCase):
    """`Dashboard.post` da página `home`, com dados que o formulário do tipo recusa."""

    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser('wd-root', 'wd-root@example.com', 'not-a-real-password')
        cls.blind = _staff('wd-blind')
        cls.actors = (('root', cls.root), ('blind', cls.blind))
        cls.widgets = {}
        for name, user in cls.actors:
            for widget_type in ('html', 'qbutton', 'list', 'addform', 'chart', 'bookmark'):
                cls.widgets[(name, widget_type)] = _widget(user, widget_type, title='before')

    def test_invalid_data_on_an_own_widget_answers_400_with_no_body_and_saves_nothing(self):
        """AC-8987-1: dados que o formulário do tipo recusa, no widget próprio, dão 400 sem corpo e não gravam nada."""
        # controle positivo: o `html` próprio com dados válidos salva (o salvar e a conferência do banco funcionam)
        for name, user in self.actors:
            html = self.widgets[(name, 'html')]
            response = self._client_for(user).post(URL, {'id': html.pk, 'title': 'after', 'content': '<p>after</p>'})
            self.assertEqual(response.status_code, 200, msg=f'fixture: {name} saves a valid html widget')
            self.assertEqual(_value(html)['title'], 'after', msg=f'fixture: the save of {name} reaches the database')
        before = _rows()
        cases = 0
        for name, user in self.actors:
            for widget_type, datas in INVALID_DATA.items():
                for data in datas:
                    with self.subTest(actor=name, widget_type=widget_type, data=data):
                        cases += 1
                        widget = self.widgets[(name, widget_type)]
                        response = self._client_for(user).post(URL, dict(data, id=widget.pk))
                        self.assertEqual(response.status_code, 400,
                                         msg='data the form of the type refuses must answer 400')
                        self.assertEqual(response.content, b'', msg='the 400 has no body')
                        self.assertEqual(_rows(), before, msg='nothing may be saved')
        self.assertTrue(cases, msg='the loop must run')

    def test_valid_model_widgets_of_the_superuser_keep_being_saved(self):
        """AC-8930-11 (controle): a lista, o adicionar e o gráfico próprios, com um modelo registrado, gravam e respondem o painel."""
        for widget_type in MODEL_TYPES:
            with self.subTest(widget_type=widget_type):
                widget = self.widgets[('root', widget_type)]
                response = self._client_for(self.root).post(
                    URL, {'id': widget.pk, 'title': 'after', 'model': AUTHOR})
                self.assertEqual(response.status_code, 200)
                value = _value(widget)
                self.assertEqual((value['title'], value['model']), ('after', AUTHOR))

    def test_valid_quick_button_widget_of_the_superuser_keeps_being_saved(self):
        """AC-8930-11 (controle): o salvar válido do botão rápido grava e responde o painel.

        O widget de favoritos não entra: o `setup` dele não funciona com dado nenhum, nem com um favorito
        válido (`xadmin/plugins/bookmark.py:206-207` passa o request ao construtor da view, e o Django 5.2
        recusa: `TypeError`), então não é «um tipo de widget que o usuário pode usar» (follow-up F1).
        """
        registered = {widget.widget_type for widget in widget_manager.get_widgets('home')}
        self.assertIn('qbutton', registered, msg='fixture: the type is registered on the home page')
        widget = self.widgets[('root', 'qbutton')]
        response = self._client_for(self.root).post(URL, {'id': widget.pk, 'title': 'after', 'url': URL})
        self.assertEqual(response.status_code, 200, msg='a valid save answers the dashboard')
        value = _value(widget)
        self.assertEqual((value['title'], value['url']), ('after', URL), msg='the saved widget keeps what was sent')


class PermissionTests(_ClientMixin, TestCase):
    """O 403 de quem não tem a permissão do widget, com dados válidos (o formulário valida antes do `setup`)."""

    @classmethod
    def setUpTestData(cls):
        cls.blind = _staff('wd-perm-blind')
        cls.viewer = _staff('wd-perm-viewer', 'view_author')
        cls.widgets = {}
        for name, user in (('blind', cls.blind), ('viewer', cls.viewer)):
            for widget_type in ('list', 'addform'):
                cls.widgets[(name, widget_type)] = _widget(user, widget_type, title='before', model=AUTHOR)

    def test_valid_data_without_the_permission_of_the_widget_keeps_answering_403(self):
        """AC-8987-3: sem a permissão do widget, dados válidos seguem com 403 e o widget não é gravado."""
        data = {'title': 'after', 'model': AUTHOR}
        # controle positivo: com a permissão de ver o autor, o próprio `list` salva (200, grava)
        viewer_list = self.widgets[('viewer', 'list')]
        response = self._client_for(self.viewer).post(URL, dict(data, id=viewer_list.pk))
        self.assertEqual(response.status_code, 200, msg='fixture: view_author saves the own list widget')
        self.assertEqual(_value(viewer_list)['title'], 'after', msg='fixture: the save reaches the database')
        before = _rows()
        # o `addform` pede a permissão de adicionar (`add_author`), que o `viewer` não tem
        for name, user, widget_type in (('blind', self.blind, 'list'), ('blind', self.blind, 'addform'),
                                        ('viewer', self.viewer, 'addform')):
            with self.subTest(actor=name, widget_type=widget_type):
                widget = self.widgets[(name, widget_type)]
                response = self._client_for(user).post(URL, dict(data, id=widget.pk))
                self.assertEqual(response.status_code, 403, msg='without the permission of the widget: 403')
                self.assertEqual(_rows(), before, msg='nothing may be saved')


class WizardParamsTests(_ClientMixin, TestCase):
    """O passo 2 («Widget Params») do assistente de adicionar widget, que usa o mesmo campo de modelo."""

    # (nome do caso, parâmetros do passo 2): ausente é o POST sem a chave `model`
    CASES = (
        ('absent', {}),
        ('empty', {'step_1-model': ''}),
        ('no dot', {'step_1-model': 'zzz'}),
        ('too many dots', {'step_1-model': 'a.b.c'}),
        ('unknown app', {'step_1-model': 'nope.nope'}),
        ('unknown model', {'step_1-model': 'xadmin_fixture.nonexistent'}),
        ('unregistered model', {'step_1-model': 'contenttypes.contenttype'}),
    )

    @classmethod
    def setUpTestData(cls):
        cls.adder = _staff('wd-adder', 'add_userwidget')

    def _open_params_step(self, client, widget_type):
        """Passa pelo passo 1 («Widget Type») com o tipo dado e devolve o nome do campo de controle."""
        page = client.get(ADD_URL, {'page_id': 'home'})
        self.assertEqual(page.status_code, 200, msg='fixture: the add form opens')
        field = _step_field(page.content.decode('utf-8'))
        response = client.post(ADD_URL + '?page_id=home', {
            'step_0-page_id': 'home', 'step_0-widget_type': widget_type, field: 'Widget Type'})
        self.assertEqual(response.status_code, 200, msg='fixture: the type step is accepted')
        self.assertEqual(_current_step(response.content.decode('utf-8')), PARAMS_STEP,
                         msg='fixture: the wizard reaches the params step')
        return field

    def test_params_step_with_an_invalid_model_shows_the_field_error_and_creates_no_widget(self):
        """AC-8987-2: o passo de parâmetros com o modelo ausente, vazio ou não registrado volta com o erro no campo e não cria o widget."""
        cases = 0
        for widget_type in MODEL_TYPES:
            for label, params in self.CASES:
                with self.subTest(widget_type=widget_type, model=label):
                    cases += 1
                    client = self._client_for(self.adder)
                    field = self._open_params_step(client, widget_type)
                    response = client.post(ADD_URL + '?page_id=home',
                                           dict(params, **{field: PARAMS_STEP, 'step_1-title': 'wiz'}))
                    self.assertEqual(response.status_code, 200, msg='the params step shows again, not a 500')
                    body = response.content.decode('utf-8')
                    self.assertEqual(_current_step(body), PARAMS_STEP, msg='the wizard stays on the params step')
                    form = response.context['form']
                    self.assertIn('model', form.errors, msg='the error is on the model field')
                    self.assertIn(escape(form.errors['model'][0]), body, msg='the page shows the error of the field')
                    self.assertEqual(UserWidget.objects.count(), 0, msg='no widget may be created')
        self.assertTrue(cases, msg='the loop must run')

    def test_params_step_with_a_registered_model_creates_the_widget(self):
        """AC-8987-2 (controle): o passo de parâmetros com o modelo registrado cria o widget e volta ao painel."""
        for widget_type in MODEL_TYPES:
            with self.subTest(widget_type=widget_type):
                UserWidget.objects.all().delete()
                client = self._client_for(self.adder)
                field = self._open_params_step(client, widget_type)
                response = client.post(ADD_URL + '?page_id=home', {
                    field: PARAMS_STEP, 'step_1-title': 'wiz', 'step_1-model': AUTHOR})
                self.assertEqual(response.status_code, 302)
                widget = UserWidget.objects.get()
                self.assertEqual(widget.widget_type, widget_type)
                self.assertEqual(json.loads(widget.value), {'title': 'wiz', 'model': AUTHOR})


class OpenDashboardTests(_ClientMixin, TestCase):
    """Abrir o painel com um widget salvo: o `get_widgets` apaga o do modelo que sumiu e só ele."""

    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser('wd-open-root', 'wd-open-root@example.com', 'not-a-real-password')

    def test_a_saved_widget_whose_model_no_longer_exists_is_dropped_and_deleted(self):
        """AC-8987-4: o widget salvo cujo modelo deixou de existir some do painel e do banco, sem erro."""
        cases = 0
        for widget_type in MODEL_TYPES:
            for model in GONE_MODELS:
                with self.subTest(widget_type=widget_type, model=model):
                    cases += 1
                    response, control, target = _open_dashboard(self, self.root, widget_type, {'model': model})
                    self.assertEqual(response.status_code, 200, msg='the dashboard opens without an error')
                    body = response.content.decode('utf-8')
                    self.assertIn('zz-control-title', body, msg='fixture: the control widget is on the page')
                    self.assertNotIn('zz-target-title', body, msg='the widget of the gone model is not on the page')
                    self.assertFalse(UserWidget.objects.filter(pk=target.pk).exists(),
                                     msg='the widget of the gone model is deleted')
                    self.assertTrue(UserWidget.objects.filter(pk=control.pk).exists())
        self.assertTrue(cases, msg='the loop must run')

    def test_a_saved_widget_with_any_other_invalid_data_stays_in_the_database(self):
        """AC-8987-4: só o widget de modelo que sumiu é apagado: o salvo com outro dado inválido continua no banco."""
        cases = (
            ('list', {'model': 'zzz'}),
            ('list', {}),
            ('list', {'model': UNREGISTERED_MODELS[0]}),
            ('list', {'model': UNREGISTERED_MODELS[1]}),
            ('qbutton', {}),
        )
        for widget_type, value in cases:
            with self.subTest(widget_type=widget_type, value=value):
                response, control, target = _open_dashboard(self, self.root, widget_type, value)
                self.assertEqual(response.status_code, 200, msg='the dashboard opens without an error')
                self.assertIn('zz-control-title', response.content.decode('utf-8'),
                              msg='fixture: the control widget is on the page')
                self.assertTrue(UserWidget.objects.filter(pk=target.pk).exists(),
                                msg='a widget with other invalid data stays in the database')

    def test_a_saved_widget_with_a_registered_model_is_shown(self):
        """AC-8987-4 (controle): o widget salvo com um modelo registrado aparece no painel e fica no banco."""
        response, control, target = _open_dashboard(self, self.root, 'list', {'model': AUTHOR})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('zz-control-title', body)
        self.assertIn('zz-target-title', body, msg='the widget of a registered model is on the page')
        self.assertTrue(UserWidget.objects.filter(pk=target.pk).exists())


class ModelDashboardTests(_ClientMixin, TestCase):
    """O `ModelDashboard` (`<object_id>/dashboard/`) herda o `post` e o `get_widgets`: um caso de cada, no contexto do host.

    No host com `NEW_TEMPLATES_ENABLED=on` o `IndexView` não chama o `get_widgets`; o `ModelDashboard` chama.
    """

    PAGE = 'model:xadmin_fixture/author'

    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser('wd-model-root', 'wd-model-root@example.com', 'not-a-real-password')
        cls.author = Author.objects.create(name='dashboard-author')
        cls.url = '/xadmin/xadmin_fixture/author/%d/dashboard/' % cls.author.pk
        cls.widgets = {widget_type: _widget(cls.root, widget_type, cls.PAGE, title='before')
                       for widget_type in ('html', 'qbutton', 'list')}

    def test_invalid_data_on_an_own_widget_answers_400_with_no_body_and_saves_nothing(self):
        """AC-8987-1: no painel do objeto, dados que o tipo recusa dão 400 sem corpo e não gravam nada."""
        html = self.widgets['html']
        response = self._client_for(self.root).post(self.url, {'id': html.pk, 'title': 'after', 'content': '<p>c</p>'})
        self.assertEqual(response.status_code, 200, msg='fixture: the model dashboard saves a valid html widget')
        self.assertEqual(_value(html)['title'], 'after', msg='fixture: the save reaches the database')
        before = _rows()
        for widget_type, data in (('qbutton', {}), ('list', {'model': 'zzz'}), ('list', {'model': GONE_MODELS[0]})):
            with self.subTest(widget_type=widget_type, data=data):
                response = self._client_for(self.root).post(self.url, dict(data, id=self.widgets[widget_type].pk))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.content, b'', msg='the 400 has no body')
                self.assertEqual(_rows(), before, msg='nothing may be saved')

    def test_a_saved_widget_whose_model_no_longer_exists_is_dropped_and_deleted(self):
        """AC-8987-4: no painel do objeto, o widget salvo de modelo que sumiu some do painel e do banco, sem erro."""
        response, control, target = _open_dashboard(
            self, self.root, 'list', {'model': GONE_MODELS[0]}, url=self.url, page_id=self.PAGE)
        self.assertEqual(response.status_code, 200, msg='the model dashboard opens without an error')
        body = response.content.decode('utf-8')
        self.assertIn('zz-control-title', body, msg='fixture: the control widget is on the page')
        self.assertNotIn('zz-target-title', body)
        self.assertFalse(UserWidget.objects.filter(pk=target.pk).exists(), msg='the widget of the gone model is deleted')


class ModelChoiceFieldTests(SimpleTestCase):
    """O campo de modelo isolado: robustez do desenho (sem ID: o critério é o que o usuário vê)."""

    def _code(self, value):
        """O código do `ValidationError` que o campo levanta para o valor; outra exceção falha no assert, não vira ERROR."""
        try:
            ModelChoiceField().clean(value)
        except ValidationError as error:
            return error.code
        except Exception as error:
            self.fail(f'{value!r} raised {type(error).__name__} instead of a validation error')
        self.fail(f'{value!r} must be refused with a validation error')

    def test_value_that_is_missing_or_empty_is_required(self):
        for value in (None, ''):
            with self.subTest(value=value):
                self.assertEqual(self._code(value), 'required')

    def test_value_that_is_not_an_app_dot_model_label_is_an_invalid_choice(self):
        for value in ('zzz', 'a.b.c'):
            with self.subTest(value=value):
                self.assertEqual(self._code(value), 'invalid_choice')

    def test_label_of_an_app_or_model_that_does_not_exist_is_an_unknown_model(self):
        for value in GONE_MODELS:
            with self.subTest(value=value):
                self.assertEqual(self._code(value), 'unknown_model')

    def test_model_that_exists_and_is_not_registered_is_an_invalid_choice(self):
        for value in UNREGISTERED_MODELS:
            with self.subTest(value=value):
                self.assertEqual(self._code(value), 'invalid_choice')

    def test_registered_model_is_accepted_whatever_the_case(self):
        for value in (AUTHOR, 'XADMIN_FIXTURE.Author'):
            with self.subTest(value=value):
                self.assertIs(ModelChoiceField().clean(value), Author)


class InitWidgetTests(_ClientMixin, TestCase):
    """`get_init_widget` com `Dashboard.widgets` de código: a consequência aceita do #8987 (sem ID)."""

    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser('wd-init-root', 'wd-init-root@example.com', 'not-a-real-password')

    def _open_with(self, widgets):
        UserWidget.objects.all().delete()
        UserSettings.objects.all().delete()
        # `widgets` é atributo de classe do `Dashboard`: o `IndexView` o herda, e o patch o troca só durante o GET
        with mock.patch.object(website.IndexView, 'widgets', widgets):
            return self._client_for(self.root).get(URL)

    def test_a_configured_widget_with_a_model_that_does_not_exist_is_dropped_without_an_error(self):
        # controle positivo: o widget configurado com um modelo registrado é criado (o caminho passa pelo `get_init_widget`)
        response = self._open_with([[{'type': 'list', 'model': AUTHOR}]])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserWidget.objects.count(), 1, msg='fixture: the configured widget is created')
        response = self._open_with([[{'type': 'list', 'model': GONE_MODELS[0]}]])
        self.assertEqual(response.status_code, 200, msg='a configured widget of a gone model must not be a 500')
        self.assertEqual(UserWidget.objects.count(), 0, msg='the dropped widget leaves no row behind')
