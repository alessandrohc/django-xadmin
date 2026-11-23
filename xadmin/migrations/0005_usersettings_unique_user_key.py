import logging

from django.db import migrations, models
from django.db.models import Count, Max

logger = logging.getLogger(__name__)


def merge_duplicates(apps, schema_editor):
    """Junta, de cada (usuário, chave) repetido, as linhas na de maior id (a última gravada, #8983).

    O delete é irreversível: o valor das linhas de menor id some (a volta da migração é `noop`).
    """
    manager = apps.get_model('xadmin', 'UserSettings').objects.using(schema_editor.connection.alias)
    # o `GROUP BY` e o `filter` são do banco: no MySQL (colação `ai_ci`) `site-theme` e `Site-Theme`, `cafe` e `café`
    # são o mesmo grupo, e é a mesma comparação que a restrição fará depois (agrupar em Python juntaria menos)
    groups = list(manager.values('user_id', 'key').annotate(total=Count('id'), keep=Max('id')).filter(total__gt=1))
    # o `delete()` do modelo histórico vai linha a linha porque há receivers globais, sem `sender`, de `pre_delete` e
    # `post_delete` no produto (plus_report_hidra/signals.py:12-27, plus_seo/models/page.py:245-264): de 1 a 2
    # consultas por linha apagada, aceito porque se esperam poucas
    deleted = sum(manager.filter(user_id=g['user_id'], key=g['key']).exclude(pk=g['keep']).delete()[0] for g in groups)
    if groups:
        # mensagem do operador em inglês, como as demais do produto (o log do `migrate` conta o que foi juntado)
        logger.warning('xadmin.UserSettings: merged %d (user, key) group(s), deleted %d repeated row(s)',
                       len(groups), deleted)


class Migration(migrations.Migration):
    """Uma só linha de `UserSettings` por (usuário, chave): junta as repetidas e cria a restrição única (#8982).

    A junção vem antes da restrição. No PostgreSQL misturar `RunPython` e DDL na mesma migração só funciona porque a
    junção faz só `DELETE`; se ela passar a fazer `UPDATE`, separe em duas migrações. No MySQL, sem DDL transacional,
    uma falha do `AddConstraint` depois da junção se resolve rodando o `migrate` de novo (a junção é idempotente).
    """

    dependencies = [
        ('xadmin', '0004_normalize_action_flag'),
    ]

    operations = [
        migrations.RunPython(merge_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='usersettings',
            constraint=models.UniqueConstraint(fields=['user', 'key'], name='xadmin_usersettings_user_key_uniq'),
        ),
    ]
