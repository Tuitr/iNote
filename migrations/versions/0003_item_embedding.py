"""Вектор записи для поиска по смыслу (items.embedding).

Размерность 768 — до ответа роли A (журнал решений 2026-10-21). Вектор может быть пустым:
эмбеддер был недоступен, запись всё равно находится по словам. Индекс HNSW по косинусному
расстоянию: эмбеддер отдаёт нормированные векторы.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

EMBED_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("items", sa.Column("embedding", Vector(EMBED_DIM), nullable=True))
    op.create_index(
        "ix_items_embedding_hnsw",
        "items",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_items_embedding_hnsw", table_name="items")
    op.drop_column("items", "embedding")
    op.execute("DROP EXTENSION IF EXISTS vector")
