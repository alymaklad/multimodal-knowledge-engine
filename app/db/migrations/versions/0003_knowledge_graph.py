"""Knowledge graph: entities, their mentions in chunks, relations, and source summaries.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30 14:53:30.171183
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('entities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('normalized', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'normalized', name='uq_entity_user_name')
    )
    with op.batch_alter_table('entities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_entities_normalized'), ['normalized'], unique=False)
        batch_op.create_index(batch_op.f('ix_entities_user_id'), ['user_id'], unique=False)

    op.create_table('entity_mentions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('chunk_key', sa.String(length=128), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('entity_id', 'source_id', 'chunk_key', name='uq_entity_mention')
    )
    with op.batch_alter_table('entity_mentions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_entity_mentions_entity_id'), ['entity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_mentions_source_id'), ['source_id'], unique=False)

    op.create_table('entity_relations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('chunk_key', sa.String(length=128), nullable=False),
    sa.Column('subject_id', sa.Integer(), nullable=False),
    sa.Column('object_id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.ForeignKeyConstraint(['object_id'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subject_id'], ['entities.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('entity_relations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_entity_relations_object_id'), ['object_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_relations_source_id'), ['source_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_relations_subject_id'), ['subject_id'], unique=False)

    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.add_column(sa.Column('summary', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('summary_model', sa.String(length=128), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.drop_column('summary_model')
        batch_op.drop_column('summary')

    with op.batch_alter_table('entity_relations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_entity_relations_subject_id'))
        batch_op.drop_index(batch_op.f('ix_entity_relations_source_id'))
        batch_op.drop_index(batch_op.f('ix_entity_relations_object_id'))

    op.drop_table('entity_relations')
    with op.batch_alter_table('entity_mentions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_entity_mentions_source_id'))
        batch_op.drop_index(batch_op.f('ix_entity_mentions_entity_id'))

    op.drop_table('entity_mentions')
    with op.batch_alter_table('entities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_entities_user_id'))
        batch_op.drop_index(batch_op.f('ix_entities_normalized'))

    op.drop_table('entities')
