"""initial

Revision ID: 0001
Revises: 
Create Date: 2026-10-15 06:24:46.637636
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('check_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('shop', sa.String(length=50), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('error_kind', sa.String(length=30), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_check_results_checked_at', 'check_results', ['checked_at'], unique=False)
    op.create_table('products',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('shop', sa.String(length=50), nullable=False),
    sa.Column('public_id', sa.String(length=8), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('image_url', sa.Text(), nullable=True),
    sa.Column('current_price_sen', sa.Integer(), nullable=True),
    sa.Column('in_stock', sa.Boolean(), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_manual_check_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_check_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='active', nullable=False),
    sa.Column('consecutive_failures', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('active', 'failing', 'dead')", name='ck_products_status'),
    sa.CheckConstraint('current_price_sen >= 0', name='ck_products_price_nonneg'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id'),
    sa.UniqueConstraint('url')
    )
    op.create_index('ix_products_next_check_at', 'products', ['next_check_at'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('telegram_chat_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('is_blocked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('telegram_chat_id')
    )
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notifications_pending', 'notifications', ['next_attempt_at'], unique=False, postgresql_where=sa.text('sent_at IS NULL'))
    op.create_table('price_points',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('price_sen', sa.Integer(), nullable=False),
    sa.Column('in_stock', sa.Boolean(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('price_sen >= 0', name='ck_price_points_price_nonneg'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_price_points_product_observed', 'price_points', ['product_id', 'observed_at'], unique=False)
    op.create_table('watches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('target_price_sen', sa.Integer(), nullable=True),
    sa.Column('baseline_price_sen', sa.Integer(), nullable=True),
    sa.Column('last_alerted_price_sen', sa.Integer(), nullable=True),
    sa.Column('target_armed', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('target_price_sen > 0', name='ck_watches_target_positive'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'product_id', name='uq_watches_user_product')
    )
    op.create_index(op.f('ix_watches_product_id'), 'watches', ['product_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_watches_product_id'), table_name='watches')
    op.drop_table('watches')
    op.drop_index('ix_price_points_product_observed', table_name='price_points')
    op.drop_table('price_points')
    op.drop_index('ix_notifications_pending', table_name='notifications', postgresql_where=sa.text('sent_at IS NULL'))
    op.drop_table('notifications')
    op.drop_table('users')
    op.drop_index('ix_products_next_check_at', table_name='products')
    op.drop_table('products')
    op.drop_index('ix_check_results_checked_at', table_name='check_results')
    op.drop_table('check_results')
