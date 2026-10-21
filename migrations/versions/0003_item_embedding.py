"""Вектор записи для поиска по смыслу (items.embedding).

Размерность 768 — до ответа роли A (журнал решений 2026-10-21). Вектор может быть пустым:
эмбеддер был недоступен, запись всё равно находится по словам. Индекс HNSW по косинусному
расстоянию: эмбеддер отдаёт нормированные векторы.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

EMBED_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Сырым SQL: пакет pgvector убран из зависимостей вместе с эмбеддером (миграция 0004).
    op.execute(f"ALTER TABLE items ADD COLUMN embedding vector({EMBED_DIM})")
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
