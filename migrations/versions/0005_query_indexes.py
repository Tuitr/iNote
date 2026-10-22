"""Индексы по итогам EXPLAIN на 50 000 записей (шаг 31 S4, docs/d-explain.md).

Только добавляет индексы: схема и данные не меняются.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # История диалога шла обратным проходом по всему pk и отбрасывала чужие реплики.
    op.create_index("ix_dialog_log_user_id_id", "dialog_log", ["user_id", "id"])
    # reclaim_expired перебирал все уведомления ради единиц в статусе sending.
    op.create_index(
        "ix_notifications_lease_until",
        "notifications",
        ["lease_until"],
        postgresql_where=sa.text("status = 'sending'"),
    )
    # schedule_next перебирал все записи ради открытых серий.
    op.create_index(
        "ix_items_open_series",
        "items",
        ["id"],
        postgresql_where=sa.text("rrule IS NOT NULL AND deleted_at IS NULL AND status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ix_items_open_series", table_name="items")
    op.drop_index("ix_notifications_lease_until", table_name="notifications")
    op.drop_index("ix_dialog_log_user_id_id", table_name="dialog_log")
