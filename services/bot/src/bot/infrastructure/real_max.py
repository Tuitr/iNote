from typing import Final

ADAPTER_NOT_CHOSEN: Final = (
    "реальный адаптер MAX не выбран (maxapi или своя обёртка — открытое решение); "
    "запустите с BOT_MAX_MODE=fake"
)


# Примечание: BOT_MAX_MODE=real без выбранного адаптера — понятная ошибка на старте
class MaxAdapterNotChosenError(RuntimeError):
    # Примечание: текст ошибки фиксирован — его видно в логах compose
    def __init__(self) -> None:
        super().__init__(ADAPTER_NOT_CHOSEN)


# Примечание: место под реальный адаптер; появится после решения по maxapi (см. CLAUDE.md)
def createRealMaxGateway() -> None:
    raise MaxAdapterNotChosenError
