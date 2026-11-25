# coding=utf-8
"""Catalogos de traducao sem as mensagens dos plugins removidos no #9054. See #9085.

O #9054 apagou 11 modulos de plugin; as 31 mensagens deles (medidas por ``makemessages`` na
arvore de antes e na de depois) sairam dos ``django.po`` dos 12 idiomas e os ``django.mo`` foram
recompilados. O leitor de ``.po`` abaixo e minimo de proposito: o pacote nao depende de ``polib``.

Comando (a partir de docker-project/):
docker compose exec -T django bash -c 'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py -v 2 test_xadmin.test_locale_removed_plugins'
"""
import gettext
import pathlib
import re

from django.test import SimpleTestCase

import xadmin

LOCALE_DIR = pathlib.Path(xadmin.__file__).resolve().parent / 'locale'

# as 31 mensagens que o #9054 deixou sem uso (notes/extract/removed_by_9054.json do #9085)
REMOVED_MSGIDS = (
    '%s Charts', '1 comment was successfully %(action)s.', 'Approve selected comments', 'Bookmark',
    "Bookmark Widget, can show user's bookmark list data in widget.", 'Bookmarks', 'Charts',
    'Clean Bookmarks', 'Enter %s', 'Enter bookmark title', 'Flag selected comments', 'Is Shared',
    'Layout', 'Metadata', 'New Bookmark', 'No Bookmarks', 'Query String', 'Related Objects',
    'Remove selected comments', 'Save Bookmark', 'Save current page as Bookmark',
    'Show models simple chart.', 'Table', 'Thumbnails', 'Title', 'Url Name', 'Waiting', 'approved',
    'bookmark', 'flagged', 'removed',
)

# entradas de cada django.po sem as 31 (antes: 302 nos catalogos comuns, 342 no pt_BR, 320 no zh_Hans)
EXPECTED_ENTRIES = {
    'de_DE': 271, 'en': 271, 'es_MX': 271, 'eu': 271, 'id_ID': 271, 'ja': 271,
    'lt': 271, 'nl_NL': 271, 'pl': 271, 'pt_BR': 311, 'ru_RU': 271, 'zh_Hans': 289,
}


def _unquote(raw):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}.get(m.group(1), m.group(1)), raw)


def read_po(path):
    """Entradas ativas do .po: lista de dicts (ctxt, msgid, plural, msgstr {indice: texto}, fuzzy)."""
    entries = []
    current = None
    field = None
    fuzzy = False

    def flush():
        if current is not None and current['msgid']:
            entries.append(current)

    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('#,'):
            fuzzy = 'fuzzy' in line
            continue
        if line.startswith('#'):
            continue
        if not line.strip():
            flush()
            current, field, fuzzy = None, None, False
            continue
        quoted = re.match(r'^"(.*)"$', line)
        if quoted and current is not None and field is not None:
            key, idx = field
            if key == 'msgstr':
                current['msgstr'][idx] += _unquote(quoted.group(1))
            else:
                current[key] += _unquote(quoted.group(1))
            continue
        m = re.match(r'^(msgctxt|msgid_plural|msgid|msgstr)(?:\[(\d+)\])?\s+"(.*)"$', line)
        if not m:
            continue
        key, idx, text = m.group(1), m.group(2), _unquote(m.group(3))
        if key in ('msgctxt', 'msgid') and current is None:
            flush()
            current = {'msgctxt': None, 'msgid': '', 'msgid_plural': '', 'msgstr': {}, 'fuzzy': fuzzy}
        if key == 'msgctxt':
            current['msgctxt'] = text
            field = ('msgctxt', None)
        elif key == 'msgid':
            current['msgid'] = text
            field = ('msgid', None)
        elif key == 'msgid_plural':
            current['msgid_plural'] = text
            field = ('msgid_plural', None)
        else:
            index = int(idx) if idx is not None else 0
            current['msgstr'][index] = text
            field = ('msgstr', index)
    flush()
    return entries


class LocaleRemovedPluginsTest(SimpleTestCase):

    def _po(self, lang):
        path = LOCALE_DIR / lang / 'LC_MESSAGES' / 'django.po'
        self.assertTrue(path.is_file(), msg=f'{lang}: django.po must exist')
        return read_po(path)

    def test_expected_languages_are_the_catalogs_on_disk(self):
        """AC-9085-1 (controle): os 12 idiomas esperados sao os catalogos django.po do pacote."""
        on_disk = sorted(p.parent.parent.name for p in LOCALE_DIR.glob('*/LC_MESSAGES/django.po'))
        self.assertEqual(on_disk, sorted(EXPECTED_ENTRIES), msg='the languages with a django.po must be the 12 known ones')
        self.assertEqual(len(REMOVED_MSGIDS), len(set(REMOVED_MSGIDS)), msg='the removed list has no duplicates')
        self.assertEqual(len(REMOVED_MSGIDS), 31)

    def test_removed_messages_are_not_in_the_po(self):
        """AC-9085-1: nenhuma das 31 mensagens dos plugins removidos esta no django.po de nenhum idioma."""
        for lang in sorted(EXPECTED_ENTRIES):
            with self.subTest(language=lang):
                msgids = {e['msgid'] for e in self._po(lang)}
                self.assertTrue(msgids, msg=f'{lang}: the catalog must have entries')
                leftovers = sorted(msgids & set(REMOVED_MSGIDS))
                self.assertEqual(leftovers, [], msg=f'{lang}: removed-plugin messages still in django.po')

    def test_po_keeps_every_other_entry(self):
        """AC-9085-2: cada django.po segue com todas as outras entradas que tinha."""
        for lang, expected in sorted(EXPECTED_ENTRIES.items()):
            with self.subTest(language=lang):
                self.assertEqual(len(self._po(lang)), expected, msg=f'{lang}: number of entries of django.po')

    def test_mo_says_what_the_po_says(self):
        """AC-9085-3: o django.mo traduz cada entrada traduzida do .po como o .po diz e nao traz as 31."""
        for lang in sorted(EXPECTED_ENTRIES):
            with self.subTest(language=lang):
                mo_path = LOCALE_DIR / lang / 'LC_MESSAGES' / 'django.mo'
                self.assertTrue(mo_path.is_file(), msg=f'{lang}: django.mo must exist')
                with open(mo_path, 'rb') as handle:
                    catalog = gettext.GNUTranslations(handle)._catalog
                translated = [e for e in self._po(lang) if not e['fuzzy'] and e['msgstr'].get(0)]
                for entry in translated:
                    if entry['msgid_plural']:
                        for index, text in entry['msgstr'].items():
                            if text:
                                self.assertEqual(catalog.get((entry['msgid'], index)), text,
                                                 msg=f"{lang}: plural {index} of {entry['msgid']!r}")
                        continue
                    key = entry['msgid'] if entry['msgctxt'] is None else f"{entry['msgctxt']}\x04{entry['msgid']}"
                    self.assertEqual(catalog.get(key), entry['msgstr'][0], msg=f"{lang}: {entry['msgid']!r}")
                present = sorted(m for m in REMOVED_MSGIDS if m in catalog)
                self.assertEqual(present, [], msg=f'{lang}: removed-plugin messages still in django.mo')
