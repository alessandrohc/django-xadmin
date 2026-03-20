from django.db import migrations


def normalize_to_update(apps, schema_editor):
    """Normalizes legacy 'change' flags to 'update' to unify both audit log layers."""
    Log = apps.get_model('xadmin', 'Log')
    Log.objects.filter(action_flag='change').update(action_flag='update')


class Migration(migrations.Migration):

    dependencies = [
        ('xadmin', '0003_auto_20160715_0100'),
    ]

    operations = [
        migrations.RunPython(normalize_to_update, migrations.RunPython.noop),
    ]
