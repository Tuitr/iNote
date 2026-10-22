"""Схема БД v0: users, items, notifications, dialog_log.

Схема — из черновика D, типы и проверки утверждены Никитой 2026-10-21 (план S1, раздел 3).
Поиск по смыслу — без эмбеддера, через подсказки search_hints (миграция 0004).

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_TIMESTAMPTZ = sa.TIMESTAMP(timezone=True)
# Поиск в S2 ищет по русским словам с учётом окончаний, поэтому словарь russian.
_SEARCH_TEXT = (
    "to_tsvector('russian', "
    "coalesce(title, '') || ' ' || coalesce(notes, '') || ' ' || coalesce(source_text, ''))"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("max_user_id", sa.BigInteger, nullable=False),
        sa.Column("max_chat_id", sa.BigInteger, nullable=True),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("tz", sa.Text, nullable=False),
        sa.Column("voice_replies", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("default_event_min", sa.Integer, nullable=False, server_default="60"),
        sa.Column("remind_before_min", sa.Integer, nullable=False, server_default="15"),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("max_user_id", name="uq_users_max_user_id"),
        sa.CheckConstraint(
            "default_event_min BETWEEN 5 AND 480", name="ck_users_default_event_min"
        ),
        sa.CheckConstraint(
            "remind_before_min BETWEEN 0 AND 1440", name="ck_users_remind_before_min"
        ),
    )

    op.create_table(
        "items",
        sa.Column("id", sa.Uuid, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("starts_at", _TIMESTAMPTZ, nullable=True),
        sa.Column("ends_at", _TIMESTAMPTZ, nullable=True),
        sa.Column("all_day", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("remind_at", _TIMESTAMPTZ, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("rrule", sa.Text, nullable=True),
        sa.Column(
            "participants",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("source_text", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.Text, nullable=True),
        sa.Column("search_tsv", postgresql.TSVECTOR, sa.Computed(_SEARCH_TEXT, persisted=True)),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", _TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_items"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_items_user_id_users"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_items_user_id_idempotency_key"),
        sa.CheckConstraint("kind IN ('task', 'reminder', 'event')", name="ck_items_kind"),
        sa.CheckConstraint("status IN ('open', 'done', 'cancelled')", name="ck_items_status"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="ck_items_title_length"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_items_ends_after_starts"),
    )
    op.create_index(
        "ix_items_user_id_starts_at",
        "items",
        ["user_id", "starts_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_items_search_tsv", "items", ["search_tsv"], postgresql_using="gin")
    op.create_index(
        "ix_items_title_trgm",
        "items",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("item_id", sa.Uuid, nullable=False),
        sa.Column("fire_at", _TIMESTAMPTZ, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lease_until", _TIMESTAMPTZ, nullable=True),
        sa.Column("next_try_at", _TIMESTAMPTZ, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("sent_at", _TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_notifications_user_id_users"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], name="fk_notifications_item_id_items"),
        sa.UniqueConstraint("item_id", "fire_at", name="uq_notifications_item_id_fire_at"),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'cancelled')",
            name="ck_notifications_status",
        ),
    )
    op.create_index(
        "ix_notifications_fire_at",
        "notifications",
        ["fire_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "dialog_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("trace_id", sa.Text, nullable=False),
        sa.Column("turn_no", sa.SmallInteger, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("input_type", sa.Text, nullable=True),
        sa.Column(
            "item_ids",
            postgresql.ARRAY(sa.Uuid),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_dialog_log"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_dialog_log_user_id_users"),
        sa.UniqueConstraint("trace_id", "turn_no", name="uq_dialog_log_trace_id_turn_no"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_dialog_log_role"),
        sa.CheckConstraint("input_type IN ('voice', 'text')", name="ck_dialog_log_input_type"),
    )


def downgrade() -> None:
    op.drop_table("dialog_log")
    op.drop_table("notifications")
    op.drop_table("items")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
