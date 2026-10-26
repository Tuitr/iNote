from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from agent.infrastructure.scripted_brain import ScriptedBrain
from brain.factory import openLlmBrain
from brain.settings import LlmSettings
from contracts.agent_side import Brain

type BrainMode = Literal["scripted", "llm"]


# Примечание: Brain по AGENT_BRAIN на время жизни агента: scripted — правила без сети,
# llm — Brain B (libs/brain) с клиентом модели по LLM_PROVIDER; его HTTP-клиент закроется на выходе
@asynccontextmanager
async def openBrain(mode: BrainMode) -> AsyncIterator[Brain]:
    if mode == "scripted":
        yield ScriptedBrain()
        return
    async with openLlmBrain(LlmSettings()) as brain:
        yield brain
