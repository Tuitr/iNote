"""Поиск по участникам: участники в полнотексте и триграммах.

Поиск (шаг 18) ищет по названию, заметкам и участникам. В 0001 участников в search_tsv не было,
а триграммы строились только по title.

array_to_string в PostgreSQL помечена STABLE, а вычисляемая колонка принимает только IMMUTABLE
функции. Для text[] с постоянным разделителем результат от настроек не зависит, поэтому её
обёртка объявлена IMMUTABLE.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_SEARCH_TEXT = "title || ' ' || coalesce(notes, '') || ' ' || items_participants_text(participants)"
_SEARCH_TSV = (
    "to_tsvector('russian', "
    "title || ' ' || coalesce(notes, '') || ' ' || items_participants_text(participants)"
    " || ' ' || coalesce(source_text, ''))"
)
_SEARCH_TSV_0001 = (
    "to_tsvector('russian', "
    "coalesce(title, '') || ' ' || coalesce(notes, '') || ' ' || coalesce(source_text, ''))"
)


def upgrade() -> None:
    op.execute(
        "CREATE FUNCTION items_participants_text(participants text[]) RETURNS text "
        "LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT array_to_string(participants, ' ') $$"
    )
    op.drop_index("ix_items_title_trgm", table_name="items")
    op.drop_index("ix_items_search_tsv", table_name="items")
    op.drop_column("items", "search_tsv")
    op.add_column(
        "items",
        sa.Column("search_tsv", postgresql.TSVECTOR, sa.Computed(_SEARCH_TSV, persisted=True)),
    )
    op.add_column(
        "items", sa.Column("search_text", sa.Text, sa.Computed(_SEARCH_TEXT, persisted=True))
    )
    op.create_index("ix_items_search_tsv", "items", ["search_tsv"], postgresql_using="gin")
    op.create_index(
        "ix_items_search_text_trgm",
        "items",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_items_search_text_trgm", table_name="items")
    op.drop_index("ix_items_search_tsv", table_name="items")
    op.drop_column("items", "search_text")
    op.drop_column("items", "search_tsv")
    op.add_column(
        "items",
        sa.Column("search_tsv", postgresql.TSVECTOR, sa.Computed(_SEARCH_TSV_0001, persisted=True)),
    )
    op.create_index("ix_items_search_tsv", "items", ["search_tsv"], postgresql_using="gin")
    op.create_index(
        "ix_items_title_trgm",
        "items",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.execute("DROP FUNCTION items_participants_text(text[])")
