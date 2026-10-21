"""Таблицы БД блока D для запросов через SQLAlchemy.

Схему в базе создают миграции (migrations/), а модели её описывают. Что они не разошлись,
проверяет тест миграций (alembic check). Проверки CHECK есть только в миграции: SQLAlchemy
их не использует, а сравнение схем Alembic их не видит.
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Computed,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from libs.core.embedding import EMBED_DIM

# Те же имена ограничений, что в миграции 0001: по ним alembic check сверяет модели с базой.
_NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}
_EMPTY_ARRAY = text("'{}'")
# Копии выражений из миграции 0002: миграция не импортирует модели, чтобы не меняться вместе с ними.
# items_participants_text — IMMUTABLE-обёртка над array_to_string из той же миграции.
_SEARCH_TEXT = "title || ' ' || coalesce(notes, '') || ' ' || items_participants_text(participants)"
_SEARCH_TSV = (
    "to_tsvector('russian', "
    "title || ' ' || coalesce(notes, '') || ' ' || items_participants_text(participants)"
    " || ' ' || coalesce(source_text, ''))"
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)
    # Время в БД только со смещением (timestamptz): naive-время в базу не попадёт.
    type_annotation_map = {  # noqa: RUF012 — так SQLAlchemy задаёт типы колонок
        str: Text,
        datetime: TIMESTAMP(timezone=True),
        UUID: Uuid,
    }


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    max_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    max_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str | None]
    tz: Mapped[str]
    voice_replies: Mapped[bool] = mapped_column(server_default=false())
    default_event_min: Mapped[int] = mapped_column(server_default="60")
    remind_before_min: Mapped[int] = mapped_column(server_default="15")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ItemRow(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index(
            "ix_items_user_id_starts_at",
            "user_id",
            "starts_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_items_search_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "ix_items_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_items_search_text_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    kind: Mapped[str]
    title: Mapped[str]
    notes: Mapped[str | None]
    starts_at: Mapped[datetime | None]
    ends_at: Mapped[datetime | None]
    all_day: Mapped[bool] = mapped_column(server_default=false())
    remind_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(server_default="open")
    rrule: Mapped[str | None]
    participants: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=_EMPTY_ARRAY)
    source_text: Mapped[str | None]
    idempotency_key: Mapped[str | None]
    # Обе колонки считает база; deferred — не читать при каждом запросе, они нужны только поиску.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(_SEARCH_TSV, persisted=True), deferred=True
    )
    search_text: Mapped[str | None] = mapped_column(
        Computed(_SEARCH_TEXT, persisted=True), deferred=True
    )
    # Пусто, если эмбеддер был недоступен: такая запись находится только по словам.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), deferred=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deleted_at: Mapped[datetime | None]


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("item_id", "fire_at"),
        Index("ix_notifications_fire_at", "fire_at", postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    item_id: Mapped[UUID] = mapped_column(ForeignKey("items.id"))
    fire_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(server_default="pending")
    attempt: Mapped[int] = mapped_column(server_default="0")
    lease_until: Mapped[datetime | None]
    next_try_at: Mapped[datetime | None]
    last_error: Mapped[str | None]
    sent_at: Mapped[datetime | None]


class DialogLogRow(Base):
    __tablename__ = "dialog_log"
    __table_args__ = (UniqueConstraint("trace_id", "turn_no"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    trace_id: Mapped[str]
    turn_no: Mapped[int] = mapped_column(SmallInteger)
    role: Mapped[str]
    text: Mapped[str]
    input_type: Mapped[str | None]
    item_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), server_default=_EMPTY_ARRAY)
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
