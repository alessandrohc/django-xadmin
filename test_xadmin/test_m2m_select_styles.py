# coding=utf-8
"""``M2MSelectPlugin`` serves only the ``m2m_transfer`` style; ``m2m_dropdown`` is gone. #7566

``m2m_dropdown`` was the single entry point of the ``bootstrap-multiselect`` vendor (36 KB
of minified js and css). Sweeping the host project, no admin declares it in ``style_fields``
-- the style was never used and the vendor was loaded for nobody.

``m2m_transfer``, served by the same plugin, **is** in use (``xadmin/plugins/auth.py``,
``plus_base/publique/xadmin_site/admin/user.py``), so the plugin does not go away as a
whole. These tests pin that boundary: the transfer style keeps working, and neither the
dropdown style nor its vendor comes back.

Run (the fork suite runs under the host project's runner):
    docker compose exec django bash /opt/project/docker-project/app-gunicorn/dev-manage.sh \
        testcover test_xadmin.test_m2m_select_styles --keepdb --verbosity 2
"""
from django.test import SimpleTestCase


class M2MTransferStyleTests(SimpleTestCase):
    """The style still in use must not fall together with the removal."""

    def test_transfer_widget_still_exists(self):
        from django.forms import SelectMultiple
        from xadmin.plugins.multiselect import SelectMultipleTransfer

        self.assertTrue(issubclass(SelectMultipleTransfer, SelectMultiple))

    def test_plugin_activates_for_transfer(self):
        from xadmin.plugins.multiselect import M2MSelectPlugin

        plugin = M2MSelectPlugin.__new__(M2MSelectPlugin)
        plugin.admin_view = type('V', (), {'style_fields': {'groups': 'm2m_transfer'}})()
        self.assertTrue(plugin.init_request())

    def test_transfer_style_returns_the_transfer_widget(self):
        from django.db.models import ManyToManyField
        from xadmin.plugins.multiselect import M2MSelectPlugin, SelectMultipleTransfer

        plugin = M2MSelectPlugin.__new__(M2MSelectPlugin)
        field = ManyToManyField('auth.Group', verbose_name='groups')
        style = plugin.get_field_style({}, field, 'm2m_transfer')
        self.assertIsInstance(style['widget'], SelectMultipleTransfer)

    def test_transfer_media_does_not_pull_the_removed_vendor(self):
        from xadmin.plugins.multiselect import SelectMultipleTransfer

        js = ' '.join(str(path) for path in SelectMultipleTransfer().media._js)
        self.assertNotIn('bootstrap-multiselect', js)


class M2MDropdownRemovalTests(SimpleTestCase):
    """The dead style and the vendor only it loaded must not come back."""

    def test_dropdown_widget_is_gone(self):
        import xadmin.plugins.multiselect as multiselect

        self.assertFalse(hasattr(multiselect, 'SelectMultipleDropdown'),
                         msg='SelectMultipleDropdown was removed with the dead m2m_dropdown style')

    def test_plugin_does_not_activate_for_dropdown(self):
        from xadmin.plugins.multiselect import M2MSelectPlugin

        plugin = M2MSelectPlugin.__new__(M2MSelectPlugin)
        plugin.admin_view = type('V', (), {'style_fields': {'groups': 'm2m_dropdown'}})()
        self.assertFalse(plugin.init_request(),
                         msg='m2m_dropdown is no longer a supported style')

    def test_multiselect_vendor_is_unregistered(self):
        from xadmin.vendors import vendors

        self.assertNotIn('multiselect', vendors,
                         msg='the bootstrap-multiselect vendor entry was removed')

    def test_no_vendor_entry_points_at_bootstrap_multiselect(self):
        # Wider guard: no other vendor may reference the deleted files either.
        from xadmin.vendors import vendors

        self.assertNotIn('bootstrap-multiselect', repr(vendors))
