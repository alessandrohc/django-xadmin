# coding=utf-8
"""O seletor de tipo de widget do painel escreve o `name`, o `id` e o valor como texto. See #8989.

``WidgetTypeSelect.render`` montava o ``<input type="hidden" id="%s_input" name="%s" value="%s"/>``
por concatenação e devolvia tudo em ``mark_safe``: um ``widget_type`` com ``"`` e ``<`` voltava cru
no HTML. O seletor está em todo formulário do ``UserWidget``, e o valor chega a ele por oito
portas (medidas no planejamento do #8989): o assistente de adicionar (GET com
``widget_type`` na query, POST recusado), o formulário de adicionar sem o assistente (ajax,
``_ajax=1``, ``_ajax=`` vazio, ``_field``), o de alterar (POST recusado) e o de alterar com
``_saveasnew``. O ``render`` é o ponto único de escrita do campo, então o AC-8989-2 prova o
escape nele (unit, universal sobre o texto) e os testes de formulário provam as portas medidas.

Os ACs de regressão (``SHALL CONTINUE TO``) passam no código de antes e ficam como controle:
o assistente avança com os quatro tipos, a lista e o campo oculto saem iguais (byte a byte, contra o
HTML do commit-base ``6b5de876``), quem não tem acesso não recebe o formulário nem o valor, e um
tipo que não existe segue recusado.

O cliente é montado com ``raise_request_exception=False`` para um 500 aparecer como status e o
red cair no assert. O campo oculto é lido com o ``html.parser`` da biblioteca padrão (os atributos
decodificados), nunca por regex.

    python runtests.py test_xadmin.test_widget_type_select_escape
    # no container do projeto host:
    docker compose exec -T django bash -c \\
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_widget_type_select_escape'
"""
import json
from html.parser import HTMLParser

from django.contrib.auth.models import Permission, User
from django.test import Client, SimpleTestCase, TestCase
from django.utils import translation

from xadmin.models import UserWidget
from xadmin.views.dashboard import WidgetTypeSelect, widget_manager

ADD_URL = '/xadmin/xadmin/userwidget/add/'
AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
# chave do formulário rápido (``QuickFormPrefix``): o hash do prefixo vem do tempo, o atacante não o
# conhece, e o plugin copia o valor sob a chave que o `_field` nomeia, qualquer que seja o prefixo
QUICK_KEY = 'quickform-xadmin-userwidget-00000-widget_type'

# os quatro tipos que o pacote mantém na página `home` (medido: todos avançam o assistente); `bookmark` e `chart`
# saíram com os módulos deles no #9054, e o filtro de `_selector` mantém o HTML fixo enquanto eles ainda existirem
FOUR_TYPES = ('html', 'qbutton', 'list', 'addform')
FIVE_CHARS = ('"', "'", '<', '>', '&')
SCRIPT_PAYLOAD = '"><script>alert(1)</script>'
ATTRIBUTE_PAYLOAD = '" onfocus="alert(1)" autofocus="'
# A lista fechada dos AC-8989-1 e -5: pelo menos um dos cinco caracteres e só caracteres imprimíveis.
VALUES = FIVE_CHARS + (
    'a&amp;b&lt;',  # entidades já escritas: o escape duplo aparece como `a&b<` na leitura de volta
    'ação "x" <ü>',
    SCRIPT_PAYLOAD,
    ATTRIBUTE_PAYLOAD,
    '<b>x</b>',
    "x' onmouseover='alert(1)",
)
# o texto cru de cada um destes não pode aparecer em lugar nenhum do HTML
RAW_MARKERS = (SCRIPT_PAYLOAD, ATTRIBUTE_PAYLOAD, '<b>x</b>', "x' onmouseover='alert(1)")
# Um dos cinco caracteres junto de caractere de controle (AC-8989-2): o atributo não pode fechar.
MIXED = ('"\nonfocus="alert(1)"', '<\rx', "'\ty", '"\r\n><script>alert(1)</script>')

# O HTML do `render` no commit-base `6b5de876` (idioma `en`, os quatro tipos da página `home`, sem os dois de `bookmark` e `chart`, `name`
# `widget_type`, `id` `id_widget_type`, valor vazio). Os outros valores se derivam dele: o tipo
# escolhido ganha `nav-link  active` e o campo oculto leva o valor (conferido contra o commit-base
# para todos os oito valores usados abaixo).
BASE_EMPTY = """<ul class="nav nav-pills flex-column" id="id_widget_type" name="widget_type">

<li class="nav-item">
    <a class="nav-link " id="nav_item_id_widget_type_html">
        <h4><i class="fa fa-file-alt mr-1"></i>html</h4>
        <span class="text-dark">Html Content Widget, can write any html content in widget.</span>
    </a>
</li>
<script type="application/javascript">
    $(function() {
        var $link = $("#nav_item_id_widget_type_html");
        $link.click(function () {
            $link.parent().parent().find('a')
                .removeClass("active");
            $link.addClass("active");
            $("#id_widget_type_input").attr("value", "html");
        })
    });
</script>

<li class="nav-item">
    <a class="nav-link " id="nav_item_id_widget_type_qbutton">
        <h4><i class="fa fa-caret-square-right mr-1"></i>qbutton</h4>
        <span class="text-dark">Quick button Widget, quickly open any page.</span>
    </a>
</li>
<script type="application/javascript">
    $(function() {
        var $link = $("#nav_item_id_widget_type_qbutton");
        $link.click(function () {
            $link.parent().parent().find('a')
                .removeClass("active");
            $link.addClass("active");
            $("#id_widget_type_input").attr("value", "qbutton");
        })
    });
</script>

<li class="nav-item">
    <a class="nav-link " id="nav_item_id_widget_type_list">
        <h4><i class="fa fa-align-justify mr-1"></i>list</h4>
        <span class="text-dark">Any Objects list Widget.</span>
    </a>
</li>
<script type="application/javascript">
    $(function() {
        var $link = $("#nav_item_id_widget_type_list");
        $link.click(function () {
            $link.parent().parent().find('a')
                .removeClass("active");
            $link.addClass("active");
            $("#id_widget_type_input").attr("value", "list");
        })
    });
</script>

<li class="nav-item">
    <a class="nav-link " id="nav_item_id_widget_type_addform">
        <h4><i class="fa fa-plus mr-1"></i>addform</h4>
        <span class="text-dark">Add any model object Widget.</span>
    </a>
</li>
<script type="application/javascript">
    $(function() {
        var $link = $("#nav_item_id_widget_type_addform");
        $link.click(function () {
            $link.parent().parent().find('a')
                .removeClass("active");
            $link.addClass("active");
            $("#id_widget_type_input").attr("value", "addform");
        })
    });
</script>
</ul>
<input type="hidden" id="id_widget_type_input" name="widget_type" value=""/>"""


class _Elements(HTMLParser):
    """Coleta os `<input>` e os `<a>` do HTML, com os atributos já decodificados (lista de pares)."""

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'input':
            self.inputs.append(attrs)
        elif tag == 'a':
            self.links.append(attrs)


def _elements(body):
    parser = _Elements()
    parser.feed(body)
    parser.close()
    return parser


def _hidden_inputs(body):
    """Os `<input type="hidden">` do HTML."""
    return [attrs for attrs in _elements(body).inputs if dict(attrs).get('type') == 'hidden']


def _widget_type_inputs(body):
    """Os campos ocultos do tipo de widget: o `id` termina em `widget_type_input` (o prefixo muda por porta)."""
    return [attrs for attrs in _hidden_inputs(body) if (dict(attrs).get('id') or '').endswith('widget_type_input')]


def _wizard_step(body):
    """O valor do campo de controle do assistente (`...-current_step`) na página, ou None."""
    for attrs in _elements(body).inputs:
        attrs = dict(attrs)
        if (attrs.get('name') or '').endswith('current_step'):
            return attrs.get('value')
    return None


def _wizard_field(body):
    """O nome do campo de controle do assistente na página."""
    for attrs in _elements(body).inputs:
        attrs = dict(attrs)
        if (attrs.get('name') or '').endswith('current_step'):
            return attrs['name']
    raise AssertionError('the wizard control field is not on the page')


def _first_difference(left, right):
    """Onde dois textos longos divergem (uma falha de snapshot não pode despejar 8 mil caracteres)."""
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return 'first difference at %d: %r != %r' % (index, left[max(index - 20, 0):index + 40],
                                                         right[max(index - 20, 0):index + 40])
    return 'one text is a prefix of the other (%d != %d characters)' % (len(left), len(right))


def _staff(username, *codenames):
    """Usuário da equipe (o xadmin só deixa a equipe entrar) com exatamente as permissões dadas."""
    user = User.objects.create_user(username, f'{username}@example.com', 'not-a-real-password',
                                    is_staff=True)
    if codenames:
        user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def _selector():
    """O seletor com os quatro tipos da página `home`, na ordem do registro (os outros testes da suíte
    podem registrar mais tipos: o filtro mantém o HTML fixo)."""
    widgets = [w for w in widget_manager.get_widgets('home') if w.widget_type in FOUR_TYPES]
    widgets.sort(key=lambda w: FOUR_TYPES.index(w.widget_type))
    return WidgetTypeSelect(widgets), widgets


def _render(selector, name='widget_type', value='', id_='id_widget_type'):
    return str(selector.render(name, value, attrs={'id': id_}))


class SelectorRenderTests(SimpleTestCase):
    """O `render` chamado direto, sem banco: o escape e o HTML que não muda."""

    def test_render_writes_name_id_and_value_as_text(self):
        """AC-8989-2: `name`, `id` e valor com um dos cinco caracteres (com ou sem controle) saem como texto."""
        selector, widgets = _selector()
        self.assertEqual(len(widgets), 4, msg='fixture: the four widget types are registered')
        for text in VALUES + MIXED:
            for field in ('name', 'id', 'value'):
                with self.subTest(field=field, text=text):
                    given = {'name': 'widget_type', 'id': 'id_widget_type', 'value': 'html'}
                    given[field] = text
                    html = _render(selector, given['name'], given['value'], given['id'])
                    hidden = _hidden_inputs(html)
                    self.assertEqual(len(hidden), 1, msg='exactly one hidden input')
                    self.assertEqual(sorted(name for name, _ in hidden[0]), ['id', 'name', 'type', 'value'],
                                     msg='the text must not open an attribute')
                    attrs = dict(hidden[0])
                    self.assertEqual(attrs['id'], given['id'] + '_input')
                    self.assertEqual(attrs['name'], given['name'])
                    self.assertEqual(attrs['value'], given['value'])
                    for marker in RAW_MARKERS:
                        if text == marker:
                            self.assertTrue(marker not in html, msg='the raw text must not be in the HTML')

    def test_render_marks_only_the_chosen_type_as_active(self):
        """AC-8989-4 (controle): a lista de tipos tem ativo só o tipo igual ao valor (nenhum para vazio ou texto que não é tipo)."""
        selector, widgets = _selector()
        self.assertEqual(len(widgets), 4, msg='fixture: the four widget types are registered')
        for value in ('',) + FOUR_TYPES + ('abc',):
            with self.subTest(value=value):
                links = _elements(_render(selector, value=value)).links
                self.assertEqual(len(links), 4, msg='the four types are listed')
                active = [dict(attrs)['id'] for attrs in links
                          if 'active' in (dict(attrs).get('class') or '').split()]
                expected = ['nav_item_id_widget_type_%s' % value] if value in FOUR_TYPES else []
                self.assertEqual(active, expected)

    def test_render_hidden_input_carries_the_id_name_and_value_received(self):
        """AC-8989-8 (controle): o campo oculto leva o `id` do seletor seguido de `_input`, o `name` e o `value` recebidos."""
        selector, widgets = _selector()
        for value in ('', 'html', 'abc'):
            with self.subTest(value=value):
                hidden = _hidden_inputs(_render(selector, value=value))
                self.assertEqual(len(hidden), 1)
                self.assertEqual(dict(hidden[0]), {'type': 'hidden', 'id': 'id_widget_type_input',
                                                   'name': 'widget_type', 'value': value})

    def test_render_is_byte_for_byte_the_html_of_the_base_commit(self):
        """AC-8989-4, AC-8989-8 (controle): sem caractere especial, a saída é a do commit-base `6b5de876`, byte a byte."""
        with translation.override('en'):
            selector, widgets = _selector()
            self.assertEqual(len(widgets), 4, msg='fixture: the four widget types are registered')
            for value in ('',) + FOUR_TYPES + ('abc',):
                with self.subTest(value=value):
                    expected = BASE_EMPTY
                    if value in FOUR_TYPES:
                        expected = expected.replace(
                            'class="nav-link " id="nav_item_id_widget_type_%s"' % value,
                            'class="nav-link  active" id="nav_item_id_widget_type_%s"' % value)
                    expected = expected.replace('value=""/>', 'value="%s"/>' % value)
                    rendered = _render(selector, value=value)
                    self.assertTrue(rendered == expected, msg=_first_difference(rendered, expected))


class SelectorFormTests(TestCase):
    """O seletor dentro dos formulários reais do `UserWidget` (adicionar no assistente, ajax, alterar)."""

    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser('wts-root', 'wts-root@example.com', 'not-a-real-password')
        cls.adder = _staff('wts-adder', 'add_userwidget')
        cls.owner = _staff('wts-owner', 'change_userwidget')
        # quem tem as duas permissões: o `_saveasnew` do formulário de alterar exige as duas, e o
        # `get_object` não filtra por dono (F3 do #8989), então ele também abre o widget de outro
        cls.mallory = _staff('wts-mallory', 'add_userwidget', 'change_userwidget')
        cls.blind = _staff('wts-blind')
        cls.outsider = User.objects.create_user('wts-outsider', 'wts-outsider@example.com',
                                                'not-a-real-password', is_staff=False)
        value = json.dumps({'id': '', 'title': 'before', 'content': '<p>before</p>'})
        cls.own_widget = UserWidget.objects.create(user=cls.owner, page_id='home', widget_type='html', value=value)
        cls.mallory_widget = UserWidget.objects.create(user=cls.mallory, page_id='home', widget_type='html', value=value)
        cls.foreign_widget = UserWidget.objects.create(user=cls.blind, page_id='home', widget_type='html', value=value)

    def _client_for(self, user=None):
        """Cliente que vira o 500 em resposta; o nome não é `client`, que é o do `TestCase`."""
        client = Client(raise_request_exception=False)
        if user is not None:
            client.force_login(user)
        return client

    @staticmethod
    def _update_url(widget):
        return '/xadmin/xadmin/userwidget/%d/update/' % widget.pk

    def _step_field(self, client):
        """O nome do campo de controle do assistente: o próprio GET de adicionar o traz."""
        response = client.get(ADD_URL, {'page_id': 'home'})
        self.assertEqual(response.status_code, 200, msg='fixture: the add form opens')
        return _wizard_field(response.content.decode('utf-8'))

    def assert_value_back_as_text(self, response, value):
        """A resposta é o formulário com o campo oculto do tipo de widget lendo de volta o texto enviado."""
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        hidden = _widget_type_inputs(body)
        self.assertEqual(len(hidden), 1, msg='exactly one hidden widget type input')
        self.assertEqual(sorted(name for name, _ in hidden[0]), ['id', 'name', 'type', 'value'],
                         msg='the text must not open an attribute')
        self.assertEqual(dict(hidden[0])['value'], value, msg='the field reads back the text sent')
        if value in RAW_MARKERS:
            self.assertTrue(value not in body, msg='the raw text must not be anywhere in the HTML')

    # ---- AC-8989-1: o envio recusado volta como texto ---------------------------------------------

    def test_wizard_post_with_a_refused_type_brings_the_value_back_as_text(self):
        """AC-8989-1: o assistente de adicionar devolve o `widget_type` recusado, com um dos cinco caracteres, como texto."""
        for actor in (self.root, self.adder):
            client = self._client_for(actor)
            step = self._step_field(client)
            for value in VALUES:
                with self.subTest(actor=actor.username, value=value):
                    response = client.post(ADD_URL + '?page_id=home', {
                        'step_0-page_id': 'home', 'step_0-widget_type': value, step: 'Widget Type'})
                    self.assert_value_back_as_text(response, value)

    def test_update_post_with_a_refused_type_brings_the_value_back_as_text(self):
        """AC-8989-1: o formulário de alterar devolve o `widget_type` recusado como texto (dono, superusuário e o que altera widget alheio)."""
        for actor, widget in ((self.owner, self.own_widget), (self.root, self.own_widget),
                              (self.mallory, self.foreign_widget)):
            client = self._client_for(actor)
            for value in VALUES:
                with self.subTest(actor=actor.username, widget=widget.pk, value=value):
                    response = client.post(self._update_url(widget), {
                        'page_id': 'home', 'widget_type': value, 'value': '{}'})
                    self.assert_value_back_as_text(response, value)

    def test_saveasnew_post_with_a_refused_type_brings_the_value_back_as_text(self):
        """AC-8989-1: o `_saveasnew` do formulário de alterar refaz o assistente e devolve o `widget_type` recusado como texto."""
        client = self._client_for(self.mallory)
        step = self._step_field(client)
        for value in VALUES:
            with self.subTest(value=value):
                response = client.post(self._update_url(self.mallory_widget), {
                    '_saveasnew': '1', 'step_0-page_id': 'home', 'step_0-widget_type': value, step: 'Widget Type'})
                self.assert_value_back_as_text(response, value)

    # ---- AC-8989-5: o valor da query string volta como texto -------------------------------------

    def test_query_string_value_comes_back_as_text_on_every_add_door(self):
        """AC-8989-5: o `widget_type` da query string (assistente, ajax, `_ajax`, `_field`) volta no campo oculto como texto."""
        doors = (
            ('wizard', lambda v: {'widget_type': v}, {}),
            ('ajax header', lambda v: {'widget_type': v}, AJAX),
            ('_ajax=1', lambda v: {'_ajax': '1', 'widget_type': v}, {}),
            ('_ajax empty', lambda v: {'_ajax': '', 'widget_type': v}, {}),
            ('_field widget_type', lambda v: {'_field': 'widget_type', 'widget_type': v}, AJAX),
            ('_field step_0', lambda v: {'_field': 'step_0-widget_type', 'step_0-widget_type': v}, AJAX),
            ('_field quickform prefix', lambda v: {'_field': QUICK_KEY, QUICK_KEY: v}, AJAX),
        )
        for actor in (self.root, self.adder):
            client = self._client_for(actor)
            for label, params, extra in doors:
                for value in VALUES:
                    with self.subTest(actor=actor.username, door=label, value=value):
                        query = {'page_id': 'home'}
                        query.update(params(value))
                        self.assert_value_back_as_text(client.get(ADD_URL, query, **extra), value)

    # ---- AC-8989-3: o tipo válido avança o assistente --------------------------------------------

    def test_wizard_advances_with_the_value_the_hidden_input_carries(self):
        """AC-8989-3 (controle): com cada um dos quatro tipos, o valor que o campo oculto traz avança para a etapa dos parâmetros."""
        registered = [w.widget_type for w in widget_manager.get_widgets('home')]
        client = self._client_for(self.root)
        for widget_type in FOUR_TYPES:
            with self.subTest(widget_type=widget_type):
                self.assertIn(widget_type, registered, msg='fixture: the type is registered')
                page = client.get(ADD_URL, {'page_id': 'home', 'widget_type': widget_type})
                self.assertEqual(page.status_code, 200)
                body = page.content.decode('utf-8')
                hidden = _widget_type_inputs(body)
                self.assertEqual(len(hidden), 1)
                carried = dict(hidden[0])['value']
                self.assertEqual(carried, widget_type, msg='the hidden input carries the chosen type')
                response = client.post(ADD_URL + '?page_id=home', {
                    'step_0-page_id': 'home', 'step_0-widget_type': carried, _wizard_field(body): 'Widget Type'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(_wizard_step(response.content.decode('utf-8')), 'Widget Params')

    # ---- AC-8989-6 e AC-8989-9: quem não tem acesso não recebe o formulário nem o valor ------------

    def assert_no_form_and_no_value(self, response, status):
        self.assertEqual(response.status_code, status)
        body = response.content.decode('utf-8')
        self.assertEqual(_widget_type_inputs(body), [], msg='no widget type form')
        for marker in (SCRIPT_PAYLOAD, ATTRIBUTE_PAYLOAD):
            self.assertTrue(marker not in body, msg='the value sent must not come back')
        return body

    def test_staff_without_the_permission_gets_403_and_nothing_back(self):
        """AC-8989-6 (controle): a equipe sem a permissão de adicionar ou de alterar segue em 403, sem o formulário e sem o valor."""
        # controles: com a permissão o formulário abre e traz o campo oculto (sem isso, "sem campo" não prova o portão)
        added = self._client_for(self.adder).get(ADD_URL, {'page_id': 'home', 'widget_type': SCRIPT_PAYLOAD})
        self.assertEqual(added.status_code, 200)
        self.assertEqual(len(_widget_type_inputs(added.content.decode('utf-8'))), 1)
        changed = self._client_for(self.owner).get(self._update_url(self.own_widget))
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(len(_widget_type_inputs(changed.content.decode('utf-8'))), 1)

        cases = (
            ('blind', self.blind, ADD_URL + '?page_id=home'),
            ('owner (change only)', self.owner, ADD_URL + '?page_id=home'),
            ('blind', self.blind, self._update_url(self.own_widget)),
            ('adder (add only)', self.adder, self._update_url(self.own_widget)),
        )
        for label, actor, url in cases:
            client = self._client_for(actor)
            for payload in (SCRIPT_PAYLOAD, ATTRIBUTE_PAYLOAD):
                with self.subTest(actor=label, url=url, payload=payload):
                    self.assert_no_form_and_no_value(client.get(url, {'page_id': 'home', 'widget_type': payload}), 403)
                    self.assert_no_form_and_no_value(client.post(url, {
                        'page_id': 'home', 'widget_type': payload, 'step_0-widget_type': payload,
                        'value': '{}'}), 403)

    def test_visitor_and_non_staff_get_the_login_page_and_nothing_back(self):
        """AC-8989-9 (controle): o visitante e o usuário fora da equipe seguem na página de login, sem o formulário e sem o valor."""
        for label, actor in (('visitor', None), ('non-staff', self.outsider)):
            client = self._client_for(actor)
            for url in (ADD_URL + '?page_id=home', self._update_url(self.own_widget)):
                for payload in (SCRIPT_PAYLOAD, ATTRIBUTE_PAYLOAD):
                    with self.subTest(actor=label, url=url, payload=payload):
                        for response in (
                                client.get(url, {'page_id': 'home', 'widget_type': payload}),
                                client.post(url, {'page_id': 'home', 'widget_type': payload,
                                                  'step_0-widget_type': payload, 'value': '{}'})):
                            body = self.assert_no_form_and_no_value(response, 200)
                            # é a página de login (o campo `next` a identifica), não uma página qualquer
                            self.assertTrue(any(dict(attrs).get('name') == 'next'
                                                for attrs in _elements(body).inputs),
                                            msg='the login form is what answers')
            # `'` e `&` crus na query string: o `Client` codifica o que vai em `data`, então o valor vai
            # montado na URL; o `iri_to_uri` do `get_full_path` os deixa passar, e quem os escapa é o
            # `login.html:59` (`value="{{ next }}"`), que este caso prende
            raw = "x'onmouseover='alert(1)"
            escaped = '&amp;widget_type=x&#x27;onmouseover=&#x27;alert(1)'
            for url in (ADD_URL, self._update_url(self.own_widget)):
                with self.subTest(actor=label, url=url, raw=raw):
                    body = self.assert_no_form_and_no_value(
                        client.get('%s?page_id=home&widget_type=%s' % (url, raw)), 200)
                    self.assertTrue(escaped in body, msg='fixture: the login page carries the escaped URL')
                    self.assertTrue(raw not in body, msg='the raw quotes must not come back')

    # ---- AC-8989-7: o tipo que não existe segue recusado ------------------------------------------

    def test_a_type_that_does_not_exist_is_still_refused(self):
        """AC-8989-7 (controle): o `widget_type` que não é tipo deixa o assistente na etapa do tipo e o de alterar sem salvar, sem gravar nada."""
        client = self._client_for(self.root)
        step = self._step_field(client)
        before = UserWidget.objects.count()
        response = client.post(ADD_URL + '?page_id=home', {
            'step_0-page_id': 'home', 'step_0-widget_type': 'zzz', step: 'Widget Type'})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertEqual(_wizard_step(body), 'Widget Type', msg='the wizard stays on the type step')
        self.assertTrue('zzz is not one of the available choices' in body, msg='the field error is on the page')
        self.assertEqual(UserWidget.objects.count(), before, msg='nothing may be created')

        saved = UserWidget.objects.filter(pk=self.own_widget.pk).values('widget_type', 'value').get()
        response = self._client_for(self.owner).post(self._update_url(self.own_widget), {
            'page_id': 'home', 'widget_type': 'zzz', 'value': '{}'})
        self.assertEqual(response.status_code, 200, msg='the form comes back, no redirect')
        self.assertEqual(UserWidget.objects.filter(pk=self.own_widget.pk).values('widget_type', 'value').get(),
                         saved, msg='nothing may be changed')

    def test_a_valid_update_is_saved(self):
        """AC-8989-7 (controle): o formulário de alterar com um tipo válido salva, então "sem salvar" acima não é vazio."""
        response = self._client_for(self.owner).post(self._update_url(self.own_widget), {
            'page_id': 'home', 'widget_type': 'html', 'value': json.dumps({'id': '', 'title': 'after', 'content': 'x'})})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(json.loads(UserWidget.objects.get(pk=self.own_widget.pk).value)['title'], 'after')
