# coding=utf-8
"""O filtro xslugify troca todas as sequencias de caracteres nao-palavra. See #9000.

``re.sub(r'\\W+', '_', value, re.I)`` passava o ``re.I`` (2) como ``count``: so as duas primeiras
sequencias eram trocadas e o Python avisava com ``DeprecationWarning``. O unico uso do filtro e o
template das opcoes do seletor de tipo de widget, que monta o mesmo slug no ``id`` do link e no
seletor do script.

    python runtests.py test_xadmin.test_xslugify
    # no container do projeto:
    docker compose exec -T django bash -c 'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_xslugify'
"""
import re
import warnings

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from xadmin.templatetags.xadmin_tags import xslugify

TEMPLATE = 'xadmin/plugins/dashboard_widget_options.html'


class XslugifyFilterTest(SimpleTestCase):

    def test_plain_word_is_unchanged(self):
        """AC-9000-1 (controle): uma palavra sem sequencia nao-palavra volta igual."""
        self.assertEqual(xslugify('abc_1'), 'abc_1')

    def test_every_run_of_non_word_characters_becomes_one_underscore(self):
        """AC-9000-1: cada sequencia nao-palavra vira um unico '_', sem DeprecationWarning."""
        cases = (
            ('a-b-c-d', 'a_b_c_d'),
            ('x"y<z>w&q', 'x_y_z_w_q'),
            ('a b  c', 'a_b_c'),
            ('--a--', '_a_'),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                with warnings.catch_warnings():
                    warnings.simplefilter('error')
                    self.assertEqual(xslugify(value), expected, msg=f'slug of {value!r}')


class WidgetOptionsTemplateTest(SimpleTestCase):

    def test_link_and_script_use_the_same_slug(self):
        """AC-9000-2: o id do link e o seletor do script levam o mesmo slug, so com caracteres de palavra."""
        html = render_to_string(TEMPLATE, {
            'id': 'id_w-1-x', 'widget_type': 'a.b-c.d&e', 'widget_icon': 'fa fa-x',
            'widget_title': 'T', 'widget_description': 'D', 'is_active': False,
        })
        link = re.search(r'id="(nav_item_[^"]*)"', html)
        selector = re.search(r'\$\("#(nav_item_[^"]*)"\)', html)
        self.assertIsNotNone(link, msg='the link must carry an id nav_item_…')
        self.assertIsNotNone(selector, msg='the script must select #nav_item_…')
        self.assertEqual(link.group(1), selector.group(1), msg='link id and script selector must be the same')
        self.assertEqual(link.group(1), 'nav_item_id_w_1_x_a_b_c_d_e', msg='the slug must replace every run')
        self.assertIsNone(re.search(r'\W', link.group(1)), msg='the id must be made of word characters only')
