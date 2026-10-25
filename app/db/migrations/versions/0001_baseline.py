"""Baseline: the schema as of phase 4, when migrations were introduced.

Databases created earlier by `create_all` are brought to this revision by
`app.db.migrate.adopt_unversioned_database` and stamped, rather than recreated.

Revision ID: 0001
Revises:
Create Date: 2026-10-25 23:30:31.852603
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# PostgreSQL keeps enum types after their tables are dropped, so the downgrade removes them.
ENUM_TYPES = (
    "sourcekind",
    "sourcestatus",
    "evaluationstatus",
    "mediaassetkind",
    "jobstatus",
    "storedobjectkind",
    "stagestatus",
)


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('benchmark_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('notes', sa.String(length=2000), nullable=True),
    sa.Column('configuration', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'label', name='uq_benchmark_run_label')
    )
    with op.batch_alter_table('benchmark_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_benchmark_runs_user_id'), ['user_id'], unique=False)

    op.create_table('collections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('collections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_collections_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_collections_user_id'), ['user_id'], unique=False)

    op.create_table('sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.Enum('document', 'web', 'youtube', 'image', 'audio', 'video', 'text', name='sourcekind'), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('origin_url', sa.String(length=2048), nullable=True),
    sa.Column('original_filename', sa.String(length=500), nullable=True),
    sa.Column('mime_type', sa.String(length=255), nullable=True),
    sa.Column('content_hash', sa.String(length=255), nullable=True),
    sa.Column('author', sa.String(length=255), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('duration', sa.Float(), nullable=True),
    sa.Column('thumbnail_path', sa.String(length=2048), nullable=True),
    sa.Column('media_path', sa.String(length=2048), nullable=True),
    sa.Column('status', sa.Enum('pending', 'downloading', 'processing', 'completed', 'failed', 'needs_review', name='sourcestatus'), nullable=False),
    sa.Column('processing_version', sa.String(length=64), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'content_hash', name='uq_source_user_hash'),
    sa.UniqueConstraint('user_id', 'origin_url', name='uq_source_user_url')
    )
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sources_content_hash'), ['content_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_sources_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_sources_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_sources_user_id'), ['user_id'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=512), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('collection_sources',
    sa.Column('collection_id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('collection_id', 'source_id')
    )
    with op.batch_alter_table('collection_sources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_collection_sources_source_id'), ['source_id'], unique=False)

    op.create_table('content_embeddings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('chunk_key', sa.String(length=128), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('locator', sa.JSON(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('vector', sa.JSON(), nullable=False),
    sa.Column('dimensions', sa.Integer(), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'chunk_key', name='uq_embedding_source_chunk')
    )
    with op.batch_alter_table('content_embeddings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_content_embeddings_content_type'), ['content_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_content_embeddings_source_id'), ['source_id'], unique=False)

    op.create_table('evaluation_cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('collection_id', sa.Integer(), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=True),
    sa.Column('category', sa.String(length=64), nullable=False),
    sa.Column('ground_truth', sa.JSON(), nullable=False),
    sa.Column('prediction', sa.JSON(), nullable=True),
    sa.Column('scores', sa.JSON(), nullable=True),
    sa.Column('status', sa.Enum('pending', 'scored', name='evaluationstatus'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('evaluation_cases', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evaluation_cases_user_id'), ['user_id'], unique=False)

    op.create_table('extracted_content',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('text_representation', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'content_type', name='uq_extracted_content_type')
    )
    with op.batch_alter_table('extracted_content', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_extracted_content_content_type'), ['content_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_extracted_content_source_id'), ['source_id'], unique=False)

    op.create_table('extraction_corrections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=False),
    sa.Column('field_name', sa.String(length=128), nullable=False),
    sa.Column('previous_value', sa.JSON(), nullable=True),
    sa.Column('corrected_value', sa.JSON(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('extraction_corrections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_extraction_corrections_content_type'), ['content_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_extraction_corrections_source_id'), ['source_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_extraction_corrections_user_id'), ['user_id'], unique=False)

    op.create_table('media_assets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('audio', 'frame', name='mediaassetkind'), nullable=False),
    sa.Column('path', sa.String(length=2048), nullable=False),
    sa.Column('sequence_number', sa.Integer(), nullable=True),
    sa.Column('timestamp_seconds', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'kind', 'sequence_number', name='uq_media_asset_sequence')
    )
    with op.batch_alter_table('media_assets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_media_assets_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_media_assets_source_id'), ['source_id'], unique=False)

    op.create_table('processing_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'completed', 'failed', 'needs_review', name='jobstatus'), nullable=False),
    sa.Column('current_stage', sa.String(length=64), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_processing_jobs_source_id'), ['source_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_processing_jobs_status'), ['status'], unique=False)

    op.create_table('processing_provenance',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('processing_version', sa.String(length=64), nullable=False),
    sa.Column('models', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('processing_provenance', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_processing_provenance_source_id'), ['source_id'], unique=True)

    op.create_table('saved_searches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('collection_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('query', sa.String(length=500), nullable=False),
    sa.Column('filters', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('saved_searches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_saved_searches_collection_id'), ['collection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saved_searches_user_id'), ['user_id'], unique=False)

    op.create_table('source_segments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('modality', sa.String(length=32), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('locator', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'sequence', name='uq_segment_sequence')
    )
    with op.batch_alter_table('source_segments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_source_segments_modality'), ['modality'], unique=False)
        batch_op.create_index(batch_op.f('ix_source_segments_source_id'), ['source_id'], unique=False)

    op.create_table('stored_objects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('video', 'original', 'audio', 'frame', 'thumbnail', name='storedobjectkind'), nullable=False),
    sa.Column('storage_key', sa.String(length=1024), nullable=False),
    sa.Column('backend', sa.String(length=32), nullable=False),
    sa.Column('content_type', sa.String(length=128), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sequence_number', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'storage_key', name='uq_stored_object_source_key')
    )
    with op.batch_alter_table('stored_objects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stored_objects_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_stored_objects_source_id'), ['source_id'], unique=False)

    op.create_table('transcripts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=32), nullable=True),
    sa.Column('segments', sa.JSON(), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('transcripts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_transcripts_source_id'), ['source_id'], unique=True)

    op.create_table('visual_analyses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('analysis_json', sa.JSON(), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('visual_analyses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_visual_analyses_source_id'), ['source_id'], unique=True)

    op.create_table('benchmark_predictions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('prediction', sa.JSON(), nullable=False),
    sa.Column('scores', sa.JSON(), nullable=False),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['evaluation_cases.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['benchmark_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'case_id', name='uq_benchmark_run_case')
    )
    with op.batch_alter_table('benchmark_predictions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_benchmark_predictions_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_benchmark_predictions_run_id'), ['run_id'], unique=False)

    op.create_table('job_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['processing_jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('job_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_events_job_id'), ['job_id'], unique=False)

    op.create_table('ocr_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('frame_asset_id', sa.Integer(), nullable=True),
    sa.Column('timestamp_seconds', sa.Float(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('bounding_boxes', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['frame_asset_id'], ['media_assets.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ocr_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ocr_results_source_id'), ['source_id'], unique=False)

    op.create_table('stage_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('succeeded', 'degraded', 'skipped', 'failed', name='stagestatus'), nullable=False),
    sa.Column('duration_ms', sa.Float(), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=True),
    sa.Column('items_processed', sa.Integer(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['processing_jobs.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('stage_metrics', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stage_metrics_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stage_metrics_source_id'), ['source_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stage_metrics_stage'), ['stage'], unique=False)
        batch_op.create_index(batch_op.f('ix_stage_metrics_status'), ['status'], unique=False)

    op.create_table('task_dispatches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.String(length=255), nullable=False),
    sa.Column('task_name', sa.String(length=128), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['processing_jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('task_dispatches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_dispatches_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_dispatches_task_id'), ['task_id'], unique=True)



def downgrade() -> None:
    with op.batch_alter_table('task_dispatches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_dispatches_task_id'))
        batch_op.drop_index(batch_op.f('ix_task_dispatches_job_id'))

    op.drop_table('task_dispatches')
    with op.batch_alter_table('stage_metrics', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stage_metrics_status'))
        batch_op.drop_index(batch_op.f('ix_stage_metrics_stage'))
        batch_op.drop_index(batch_op.f('ix_stage_metrics_source_id'))
        batch_op.drop_index(batch_op.f('ix_stage_metrics_job_id'))

    op.drop_table('stage_metrics')
    with op.batch_alter_table('ocr_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ocr_results_source_id'))

    op.drop_table('ocr_results')
    with op.batch_alter_table('job_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_events_job_id'))

    op.drop_table('job_events')
    with op.batch_alter_table('benchmark_predictions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_benchmark_predictions_run_id'))
        batch_op.drop_index(batch_op.f('ix_benchmark_predictions_case_id'))

    op.drop_table('benchmark_predictions')
    with op.batch_alter_table('visual_analyses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_visual_analyses_source_id'))

    op.drop_table('visual_analyses')
    with op.batch_alter_table('transcripts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_transcripts_source_id'))

    op.drop_table('transcripts')
    with op.batch_alter_table('stored_objects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stored_objects_source_id'))
        batch_op.drop_index(batch_op.f('ix_stored_objects_kind'))

    op.drop_table('stored_objects')
    with op.batch_alter_table('source_segments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_source_segments_source_id'))
        batch_op.drop_index(batch_op.f('ix_source_segments_modality'))

    op.drop_table('source_segments')
    with op.batch_alter_table('saved_searches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saved_searches_user_id'))
        batch_op.drop_index(batch_op.f('ix_saved_searches_collection_id'))

    op.drop_table('saved_searches')
    with op.batch_alter_table('processing_provenance', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_processing_provenance_source_id'))

    op.drop_table('processing_provenance')
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_processing_jobs_status'))
        batch_op.drop_index(batch_op.f('ix_processing_jobs_source_id'))

    op.drop_table('processing_jobs')
    with op.batch_alter_table('media_assets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_media_assets_source_id'))
        batch_op.drop_index(batch_op.f('ix_media_assets_kind'))

    op.drop_table('media_assets')
    with op.batch_alter_table('extraction_corrections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_extraction_corrections_user_id'))
        batch_op.drop_index(batch_op.f('ix_extraction_corrections_source_id'))
        batch_op.drop_index(batch_op.f('ix_extraction_corrections_content_type'))

    op.drop_table('extraction_corrections')
    with op.batch_alter_table('extracted_content', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_extracted_content_source_id'))
        batch_op.drop_index(batch_op.f('ix_extracted_content_content_type'))

    op.drop_table('extracted_content')
    with op.batch_alter_table('evaluation_cases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_evaluation_cases_user_id'))

    op.drop_table('evaluation_cases')
    with op.batch_alter_table('content_embeddings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_content_embeddings_source_id'))
        batch_op.drop_index(batch_op.f('ix_content_embeddings_content_type'))

    op.drop_table('content_embeddings')
    with op.batch_alter_table('collection_sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_collection_sources_source_id'))

    op.drop_table('collection_sources')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sources_user_id'))
        batch_op.drop_index(batch_op.f('ix_sources_status'))
        batch_op.drop_index(batch_op.f('ix_sources_kind'))
        batch_op.drop_index(batch_op.f('ix_sources_content_hash'))

    op.drop_table('sources')
    with op.batch_alter_table('collections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_collections_user_id'))
        batch_op.drop_index(batch_op.f('ix_collections_name'))

    op.drop_table('collections')
    with op.batch_alter_table('benchmark_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_benchmark_runs_user_id'))

    op.drop_table('benchmark_runs')
    bind = op.get_bind()
    for name in ENUM_TYPES:
        sa.Enum(name=name).drop(bind, checkfirst=True)
