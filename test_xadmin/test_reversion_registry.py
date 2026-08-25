# coding=utf-8
"""The eager reversion registration actually registers something.

``XAdminConfig.ready()`` used to call ``xversion.register_models()`` unconditionally.
#7093 made that conditional, because excluding the xversion plugin through
``XADMIN_EXCLUDE_PLUGINS`` made the boot raise ImproperlyConfigured. A conditional call
introduces a failure mode the original did not have: a guard that never fires. Every
other test in this suite would stay green in that case, because they only prove the boot
survives.

So these two tests come as a pair. ``test_boot.py`` proves the guard SUPPRESSES the call
when the plugin is excluded; this proves it still FIRES when the plugin is loaded, which
is every real deployment.

It also exercises ``_register_model()`` walking an inline's reverse relation -- the line
that called the removed ``ForeignObjectRel.is_hidden()`` before #7093. The AST net in
test_compat.py catches a literal revert of that fix; only this actually runs the line.
"""
import reversion
from django.test import SimpleTestCase

from test_xadmin.fixtureapp.models import Author, Book


class EagerReversionRegistrationTests(SimpleTestCase):

    def test_reversion_enabled_model_is_registered_at_boot(self):
        """The positive branch of the guard in XAdminConfig.ready().

        AuthorAdmin sets reversion_enable = True, so register_models() must have
        registered Author with django-reversion by the time the suite runs. If the
        sys.modules guard stopped firing, this is the test that notices.
        """
        self.assertTrue(
            reversion.is_registered(Author),
            msg='register_models() did not run: an admin with reversion_enable=True '
                'was not registered with reversion during XAdminConfig.ready()')

    def test_inline_relation_is_followed(self):
        """_register_model() walked the inline and followed the reverse accessor.

        This is the code path that used to call ForeignObjectRel.is_hidden(), removed
        in Django 5.1. Reaching it at all is the point; the follow list is the evidence
        that the walk completed rather than raising.
        """
        self.assertTrue(reversion.is_registered(Book),
                        msg='the inline model should have been auto-registered')
        follow = reversion.revisions._registered_models[
            reversion.revisions._get_registration_key(Author)].follow
        self.assertIn('books', follow,
                      msg="the inline's reverse accessor must be followed, which is "
                          'what the .hidden check gates')
