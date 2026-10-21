"""Подмены внешних сервисов для тестов."""


class FakeRedis:
    """Redis для тестов: сервисам D в S1 нужна только команда INCR (версия данных)."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]
