"""Подмены внешних сервисов для тестов."""

import zlib

from libs.core.embedding import EMBED_DIM, EmbedderUnavailableError, EmbedKind


class FakeRedis:
    """Redis для тестов: сервисам D в S1 нужна только команда INCR (версия данных)."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]


class FakeEmbedder:
    """Эмбеддер для тестов: близкие по смыслу слова из одной группы дают один и тот же вектор.

    Настоящую модель роли A не заменяет — только проверяет, что D правильно хранит векторы,
    ищет по ним и живёт без эмбеддера.
    """

    synonyms = (
        ("стоматолог", "зубн"),
        ("машин", "автомобил"),
    )

    def __init__(self) -> None:
        self.available = True
        self.calls = 0

    async def embed(self, texts: list[str], kind: EmbedKind, timeout_s: float) -> list[list[float]]:
        self.calls += 1
        if not self.available:
            raise EmbedderUnavailableError
        return [self._vector(text.lower()) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * EMBED_DIM
        for group, words in enumerate(self.synonyms):
            if any(word in text for word in words):
                vector[group] = 1.0
        if not any(vector):
            # Текст без известных слов — в своё направление: не похож ни на группы, ни на другие.
            free = EMBED_DIM - len(self.synonyms)
            vector[len(self.synonyms) + zlib.crc32(text.encode()) % free] = 1.0
        norm = sum(value * value for value in vector) ** 0.5
        return [value / norm for value in vector]
