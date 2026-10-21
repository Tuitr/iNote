from typing import Final, Literal

from agent.infrastructure.scripted_brain import ScriptedBrain
from contracts.agent_side import Brain

type BrainMode = Literal["scripted", "llm"]

LLM_NOT_CONNECTED: Final = (
    "AGENT_BRAIN=llm: Brain участника B ещё не подключён (libs/brain появится после мержа); "
    "запустите с AGENT_BRAIN=scripted"
)


# Примечание: AGENT_BRAIN=llm до мержа с B — понятная ошибка на старте, а не тихий фолбэк
class BrainNotConnectedError(RuntimeError):
    # Примечание: текст фиксирован — его видно в логах compose
    def __init__(self) -> None:
        super().__init__(LLM_NOT_CONNECTED)


# Примечание: выбор Brain по AGENT_BRAIN; после мержа ветка llm создаёт Brain из libs/brain
def buildBrain(mode: BrainMode) -> Brain:
    if mode == "llm":
        raise BrainNotConnectedError
    return ScriptedBrain()
