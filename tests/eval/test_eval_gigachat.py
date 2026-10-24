"""Eval Brain на настоящем GigaChat — платно, только по прямой просьбе владельца B.

Запуск: LLM_PROVIDER=gigachat и LLM_GIGACHAT_CREDENTIALS в окружении, затем
`uv run pytest -m eval tests/eval`. Без ключа тест пропускается — в CI он не тратит деньги.
"""

import os
from pathlib import Path
from typing import Final

import pytest

from brain.eval.cases import EvalCase, loadCases
from brain.eval.runner import buildReport, runCases
from brain.factory import openLlmBrain
from brain.settings import LlmSettings
from contracts.agent_side import Brain

CASES_PATH: Final = Path(__file__).with_name("cases.yaml")
REPEAT: Final = 3
# Критерий готовности B: ≥ 90 % на обязательных сценариях при трёх повторах
MANDATORY_THRESHOLD: Final = 0.9

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        os.environ.get("LLM_PROVIDER") != "gigachat"
        or not os.environ.get("LLM_GIGACHAT_CREDENTIALS"),
        reason="eval на GigaChat платный: нужны LLM_PROVIDER=gigachat и ключ",
    ),
]


async def test_mandatoryScenariosAccuracy() -> None:
    settings = LlmSettings()
    async with openLlmBrain(settings) as brain:

        async def sameBrain(case: EvalCase) -> Brain:
            return brain

        records = await runCases(loadCases(CASES_PATH), sameBrain, REPEAT)
    report = buildReport(records, settings.model)
    assert float(str(report["mandatory_accuracy"])) >= MANDATORY_THRESHOLD, report
