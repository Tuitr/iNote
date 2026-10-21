"""Эмбеддер для поиска по смыслу: что D ожидает от libs/embed роли A.

ЧЕРНОВИК D, не контракт A: libs/embed ещё нет. Когда A выложит свою библиотеку, отсюда
останется только импорт из неё. Размерность 768 (как у multilingual-e5-base) выбрал Никита
2026-10-21 до ответа A; другая размерность = новая миграция и пересчёт векторов.
"""

import asyncio
import logging
from typing import Literal, Protocol

EMBED_DIM = 768

EmbedKind = Literal["query", "document"]


class EmbedderUnavailableError(Exception):
    """Эмбеддер недоступен или не уложился в таймаут: запись сохраняется без вектора."""


class Embedder(Protocol):
    async def embed(self, texts: list[str], kind: EmbedKind, timeout_s: float) -> list[list[float]]:
        """Вектор длины EMBED_DIM на каждый текст, в том же порядке, нормированный (длина 1)."""
        ...


# Сколько ждём эмбеддер при создании записи и поиске: ответ агенту не должен тормозить.
EMBED_TIMEOUT_SECONDS = 2.0

logger = logging.getLogger(__name__)


def item_text(
    title: str, notes: str | None, participants: list[str], source_text: str | None
) -> str:
    """Текст записи, по которому считается её вектор."""
    return " ".join(part for part in (title, notes, *participants, source_text) if part)


async def embed_or_none(
    embedder: Embedder | None, text: str, kind: EmbedKind
) -> list[float] | None:
    """Вектор текста или None, если эмбеддера нет или он не ответил: поиск тогда идёт по словам.

    Таймаут D держит и сам: зависший эмбеддер не должен подвесить создание записи.
    """
    if embedder is None:
        return None
    try:
        async with asyncio.timeout(EMBED_TIMEOUT_SECONDS):
            [vector] = await embedder.embed([text], kind, EMBED_TIMEOUT_SECONDS)
    except (EmbedderUnavailableError, TimeoutError) as error:
        logger.warning("Вектор не посчитан (%s): %s", kind, type(error).__name__)
        return None
    return vector
