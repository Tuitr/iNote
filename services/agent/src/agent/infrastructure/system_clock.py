from datetime import UTC, datetime


# Примечание: часы контейнера в UTC; пояс пользователя применяет HandleMessage
class SystemClock:
    # Примечание: aware-время — наивного времени в коде нет (правило 2)
    def now(self) -> datetime:
        return datetime.now(UTC)
