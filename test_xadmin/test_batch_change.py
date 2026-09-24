# coding=utf-8
"""An invalid batch-change POST must say what is wrong. #7637

``BatchChangeAction.do_action`` re-renders the batch form when it does not validate. That
part is right: the page stays so the user can fix the input. What was missing is the
feedback. Measured on the host project (Django 5.2.17), in two different actions:

- the "Please correct the error below." alert of ``batch_change_form.html`` depends on
  ``errors`` in the context, and ``do_action`` never put it there;
- crispy renders each field error in a ``.invalid-feedback`` block, which Bootstrap 4
  hides unless it follows an ``.is-invalid`` sibling. The siblings are the
  ``ChangeFieldWidgetWrapper`` markup (``div.custom-switch`` + ``div.control-wrap``) or a
  host widget's own markup -- never ``.is-invalid``.

So the error text was in the HTML and nobody could see it: the user clicked "Change
Multiple", the page came back unchanged and nothing was saved.

The contract pinned here: ``do_action`` hands ``errors`` to the template, with the shape of
``ModelFormAdminView.get_error_list``, and the alert lists every error next to its field
label, so the message shows no matter what the field markup does.

The action is built the way ``ListAdminView`` builds it (``get_view_class`` + ``setup`` +
``init_action``), on an option class merged over the registered ``AuthorAdmin``; the
registered admin itself is left alone.

Run (the fork's own runner, sqlite):
    docker compose exec django bash -c \
        'cd /opt/project/packages/django-xadmin && /opt/project/pyenv/bin/python runtests.py test_xadmin.test_batch_change'
"""
from html.parser import HTMLParser
from types import SimpleNamespace

from django import forms
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase

from test_xadmin.fixtureapp.models import Author
from xadmin.plugins.actions import ACTION_CHECKBOX_NAME
from xadmin.plugins.batch import BATCH_CHECKBOX_NAME, BatchChangeAction
from xadmin.sites import site

CORRECT_ONE = 'Please correct the error below.'
CORRECT_MANY = 'Please correct the errors below.'

# Elements without an end tag; they must not open a nesting level in the parser below.
VOID_ELEMENTS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
                 'source', 'track', 'wbr'}


class _DangerAlerts(HTMLParser):
    """Text of every ``.alert-danger`` block, entities decoded, whitespace collapsed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.alerts = []
        self._depth = 0
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if self._depth:
            if tag not in VOID_ELEMENTS:
                self._depth += 1
            return
        if 'alert-danger' in (dict(attrs).get('class') or '').split():
            self._depth = 1
            self._chunks = []

    def handle_endtag(self, tag):
        if not self._depth:
            return
        self._depth -= 1
        if not self._depth:
            self.alerts.append(' '.join(' '.join(self._chunks).split()))

    def handle_data(self, data):
        if self._depth:
            self._chunks.append(data)


def correction_alert(html):
    """The alert that asks the user to correct the form, or None when there is none."""
    parser = _DangerAlerts()
    parser.feed(html)
    parser.close()
    return next((text for text in parser.alerts if CORRECT_ONE in text or CORRECT_MANY in text), None)


class EchoChoiceForm(forms.ModelForm):
    """Batch form whose error message repeats the posted value (invalid choice)."""
    flavor = forms.ChoiceField(choices=[('plain', 'Plain')])

    class Meta:
        model = Author
        fields = ['name']


class BatchChangeErrorsTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(
            'batch-admin', 'batch-admin@example.com', 'not-a-real-password')
        cls.author = Author.objects.create(name='ana')

    def build_action(self, data, batch_form=None, **options):
        request = RequestFactory().post('/xadmin/xadmin_fixture/author/', data)
        request.user = self.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        option_class = type('AuthorBatchOptions', (site.get_registry(Author),), options)
        # nocache: the site caches merged classes by option class NAME, and every test
        # merges its own options under the same name.
        action = site.get_view_class(BatchChangeAction, option_class, nocache=True)()
        action.setup(request)
        # init_action only reads admin_site from the list view.
        action.init_action(SimpleNamespace(admin_site=site))
        if batch_form is not None:
            # get_change_form reads batch_form from the EDIT view, not from the action.
            action.edit_view.batch_form = batch_form
        return action

    def do_action(self, data, **kwargs):
        data = dict({'action': BatchChangeAction.action_name,
                     ACTION_CHECKBOX_NAME: [str(self.author.pk)]}, **data)
        action = self.build_action(data, **kwargs)
        return action.do_action(Author.objects.filter(pk=self.author.pk))

    def post_required_name(self, **fields):
        """Save POST with ``name`` marked, required and empty -- invalid on purpose."""
        data = {'post': 'yes', BATCH_CHECKBOX_NAME: ['name'], 'name': ''}
        data.update(fields)
        return self.do_action(data, batch_fields=('name',), batch_fields_required=('name',))

    def test_invalid_post_puts_errors_in_the_context(self):
        response = self.post_required_name()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data.get('errors'),
                        msg='do_action must hand the form errors to the template')

    def test_invalid_post_shows_the_correction_alert(self):
        response = self.post_required_name()
        alert = correction_alert(response.render().content.decode())
        self.assertIsNotNone(alert, msg='an invalid batch POST must show the correction alert')
        self.assertIn(CORRECT_ONE, alert)

    def test_alert_lists_the_field_label_with_its_error(self):
        response = self.post_required_name()
        alert = correction_alert(response.render().content.decode()) or ''
        self.assertIn('Name', alert, msg='the alert must name the field that failed')
        self.assertIn('This field is required.', alert,
                      msg='the alert must carry the error itself, not only the generic sentence')

    def test_alert_lists_every_invalid_field(self):
        data = {'post': 'yes', BATCH_CHECKBOX_NAME: ['name', 'secret'], 'name': '', 'secret': ''}
        response = self.do_action(data, batch_fields=('name', 'secret'),
                                  batch_fields_required=('name', 'secret'))
        alert = correction_alert(response.render().content.decode()) or ''
        self.assertIn(CORRECT_MANY, alert, msg='two invalid fields take the plural sentence')
        self.assertIn('Name', alert)
        self.assertIn('Secret', alert)

    def test_posted_value_echoed_by_the_error_is_escaped(self):
        payload = '<script>alert(1)</script>'
        data = {'post': 'yes', BATCH_CHECKBOX_NAME: ['flavor'], 'flavor': payload}
        response = self.do_action(data, batch_form=EchoChoiceForm, batch_fields=('flavor',))
        html = response.render().content.decode()
        self.assertNotIn(payload, html, msg='the posted value must never reach the page as markup')
        self.assertIn(payload, correction_alert(html) or '',
                      msg='the alert must show the error, with the posted value as text')

    def test_first_render_shows_no_alert(self):
        # first click on the action: nothing was posted yet, there is nothing to correct
        response = self.do_action({}, batch_fields=('name',))
        self.assertFalse(response.context_data.get('errors'))
        self.assertIsNone(correction_alert(response.render().content.decode()))

    def test_valid_post_returns_none_so_the_list_redirects(self):
        data = {'post': 'yes', BATCH_CHECKBOX_NAME: ['name'], 'name': 'bea'}
        result = self.do_action(data, batch_fields=('name',))
        self.assertIsNone(result, msg='ActionPlugin redirects to the list only when the action returns None')
        self.author.refresh_from_db()
        self.assertEqual(self.author.name, 'bea')
