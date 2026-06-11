"""Add tags and source columns to cases

Revision ID: a1b2c3d4e5f6
Revises: 5f68922892b4
Create Date: 2026-02-12 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '5f68922892b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cases', sa.Column('tags', sa.JSON(), nullable=True))
    op.add_column('cases', sa.Column('source', sa.String(), nullable=True, server_default='user-reported'))
    op.create_index(op.f('ix_cases_source'), 'cases', ['source'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_cases_source'), table_name='cases')
    op.drop_column('cases', 'source')
    op.drop_column('cases', 'tags')
