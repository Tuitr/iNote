# Примечание: marker в памяти — для фейкового MAX, у которого очередь тоже живёт только в процессе
class InMemoryMarkerStore:
    # Примечание: начальное значение — «ещё не полученные»
    def __init__(self) -> None:
        self._marker: int | None = None

    # Примечание: последний зафиксированный marker
    async def load(self) -> int | None:
        return self._marker

    # Примечание: запомнить marker
    async def save(self, marker: int) -> None:
        self._marker = marker
