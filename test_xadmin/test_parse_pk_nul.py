# coding=utf-8
"""O parse_pk recusa o byte NUL em chave primaria de texto. See #9027.

``parse_pk`` converte o id de um POST pelo campo da chave do modelo. Uma chave de texto aceita
tudo, inclusive o NUL: o PostgreSQL o recusa (500) e o MySQL o ignora na colacao, entao
``foo\\x00`` casaria com ``foo``. Nenhum modelo registrado no xadmin tem chave de texto (#8985),
por isso o teste exercita so a conversao, com o ``Session`` do Django (chave ``session_key``).

    python runtests.py test_xadmin.test_parse_pk_nul
    # no container do projeto:
    docker compose exec -T django bash -c 'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_parse_pk_nul'
"""
import uuid

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import SimpleTestCase

from xadmin.util import parse_pk

from test_xadmin.fixtureapp.models import Tag


class ParsePkTextKeyNulTest(SimpleTestCase):

    def test_text_key_without_nul_is_converted(self):
        """AC-9027-1 (controle): a chave de texto sem NUL passa como texto."""
        self.assertEqual(parse_pk(Session, 'abc'), 'abc')

    def test_nul_in_a_text_key_is_refused(self):
        """AC-9027-1: o valor com o byte NUL levanta ValueError (no fim, no meio, so o NUL)."""
        for value in ('abc\x00', 'ab\x00c', '\x00'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError, msg=f'{value!r} must be refused'):
                    parse_pk(Session, value)


class ParsePkOtherKeysTest(SimpleTestCase):

    def test_integer_key_converts_what_the_field_accepts(self):
        """AC-9027-2: a chave inteira converte pelo campo (' 5' e '1_0' sao numeros)."""
        for value, expected in ((' 5', 5), ('1_0', 10), ('7', 7), (3, 3)):
            with self.subTest(value=value):
                self.assertEqual(parse_pk(User, value), expected)

    def test_integer_key_refuses_what_the_field_does_not_accept(self):
        """AC-9027-2: texto que nao e numero levanta ValueError na chave inteira."""
        for value in ('abc', '', '1\x00'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError, msg=f'{value!r} must be refused'):
                    parse_pk(User, value)

    def test_uuid_key_accepts_a_uuid_and_refuses_the_rest(self):
        """AC-9027-2: a chave UUID devolve o UUID e recusa o que nao e UUID."""
        value = uuid.uuid4()
        self.assertEqual(parse_pk(Tag, str(value)), value)
        for bad in ('x', '', str(value) + '\x00'):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError, msg=f'{bad!r} must be refused'):
                    parse_pk(Tag, bad)
