"""historico_compra"""
import sqlalchemy as sa
import sqlmodel
from alembic import op


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('movestoque', sa.Column('valor', sa.Numeric(14, 2), nullable=True))
    op.add_column('movestoque', sa.Column('data', sa.Date(), nullable=True))
    op.add_column('movestoque', sa.Column('fornecedor', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))
    # compras de rolo já registradas: copia preço/data/fornecedor do rolo
    op.execute("""UPDATE movestoque SET valor = r.preco, data = r.comprado_em, fornecedor = r.fornecedor
                  FROM rolo r WHERE movestoque.item_tipo = 'rolo' AND movestoque.motivo = 'compra' AND movestoque.ref_id = r.id""")


def downgrade():
    op.drop_column('movestoque', 'fornecedor')
    op.drop_column('movestoque', 'data')
    op.drop_column('movestoque', 'valor')
