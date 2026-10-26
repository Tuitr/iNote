"""Подсказки для поиска по смыслу (search_hints) от LLM агента.

При создании записи LLM пишет синонимы и близкие слова, они ищутся обычным полнотекстом
и триграммами — эмбеддер не нужен (решение команды 2026-10-22).

Номер 0004 оставлен, хотя идёт сразу после 0002: миграцию 0003 с колонкой embedding убрали
вместе с эмбеддером, а базы, накатанные до 0004, уже совпадают с этой схемой.

Revision ID: 0004
Revises: 0002
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0002"
branch_labels = None
depends_on = None

_TEXT_0002 = "title || ' ' || coalesce(notes, '') || ' ' || items_participants_text(participants)"
_TEXT = _TEXT_0002 + " || ' ' || coalesce(search_hints, '')"
_TSV_0002 = f"to_tsvector('russian', {_TEXT_0002} || ' ' || coalesce(source_text, ''))"
_TSV = f"to_tsvector('russian', {_TEXT} || ' ' || coalesce(source_text, ''))"


def upgrade() -> None:
    op.add_column("items", sa.Column("search_hints", sa.Text, nullable=True))
    _replace_search_columns(_TSV, _TEXT)


def downgrade() -> None:
    _replace_search_columns(_TSV_0002, _TEXT_0002)
    op.drop_column("items", "search_hints")


def _replace_search_columns(tsv: str, text: str) -> None:
    """Вычисляемые колонки нельзя изменить на месте: пересоздаём их вместе с индексами."""
    op.drop_index("ix_items_search_text_trgm", table_name="items")
    op.drop_index("ix_items_search_tsv", table_name="items")
    op.drop_column("items", "search_text")
    op.drop_column("items", "search_tsv")
    op.add_column(
        "items", sa.Column("search_tsv", postgresql.TSVECTOR, sa.Computed(tsv, persisted=True))
    )
    op.add_column("items", sa.Column("search_text", sa.Text, sa.Computed(text, persisted=True)))
    op.create_index("ix_items_search_tsv", "items", ["search_tsv"], postgresql_using="gin")
    op.create_index(
        "ix_items_search_text_trgm",
        "items",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
