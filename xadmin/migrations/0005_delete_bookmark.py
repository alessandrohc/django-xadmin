"""Remove o model ``Bookmark`` (favorito do xadmin) e derruba a tabela ``xadmin_bookmark`` (#9054).

O plugin de favoritos saiu do pacote e nenhuma instalação do produto o carrega (o ``XADMIN_EXCLUDE_PLUGINS`` do produto
o exclui), então o model e a tabela não têm mais dono. A migração é uma ``DeleteModel`` só, escrita à mão: o MySQL não
desfaz DDL, e uma DDL única não deixa estado parcial. Não mexe em ``ContentType`` nem em ``Permission`` (o content type
``xadmin.bookmark`` e as permissões dele ficam no banco).

Sem volta que devolva dado: as linhas de ``xadmin_bookmark`` são apagadas junto com a tabela, sem aviso no log, e
desfazer a migração só recria a tabela vazia.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('xadmin', '0004_normalize_action_flag'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Bookmark',
        ),
    ]
