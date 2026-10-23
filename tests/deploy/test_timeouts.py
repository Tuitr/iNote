"""Бюджет времени цепочки (S4, шаг 4): таймауты в настройках не противоречат друг другу.

Голос: скачивание → ASR (15 с, 2 повтора) → агент. Бот ждёт агента 45 с, агент укладывает ход
в 40 с вместе с ожиданием лока и вызовами MCP (5 с, 1 повтор) — иначе бот оборвёт ход, который
агент ещё доделывает, и пользователь получит «не получилось», хотя запись создана.
"""

from typing import Final

from agent.infrastructure.settings import AgentSettings
from bot.infrastructure.settings import BotSettings

# у бота к ASR 2 повтора, у агента к MCP — 1 (main.py, стандарт)
ASR_ATTEMPTS: Final = 3
MCP_ATTEMPTS: Final = 2
SECRET: Final = "t" * 32
# худший случай голосового до ответа (сейчас 10 + 3×15 + 45 = 100 с) — граница, а не цель
VOICE_WORST_S: Final = 120


# Примечание: значения по умолчанию из классов настроек (без окружения)
def defaults() -> tuple[BotSettings, AgentSettings]:
    return (
        BotSettings.model_validate({"SERVICE_TOKEN": SECRET}),
        AgentSettings.model_validate({"SERVICE_TOKEN": SECRET}),
    )


# Примечание: агент закончит ход раньше, чем бот перестанет ждать
def test_agentFinishesBeforeBotGivesUp() -> None:
    bot, agent = defaults()
    assert agent.turnTimeoutS < bot.agentTimeoutS


# Примечание: ожидание лока и один вызов MCP с повтором помещаются в бюджет хода
def test_turnBudgetHoldsLockAndMcp() -> None:
    _, agent = defaults()
    assert agent.lockWaitS + agent.mcpTimeoutS * MCP_ATTEMPTS < agent.turnTimeoutS


# Примечание: голосовое — худший случай до ответа: скачивание + ASR со всеми повторами + агент;
# конечен и укладывается в 2 минуты (стандарт: ASR 15 с и 2 повтора), «печатает…» всё это время
def test_voiceChainBounded() -> None:
    bot, _ = defaults()
    worst = bot.voiceDownloadTimeoutS + bot.asrTimeoutS * ASR_ATTEMPTS + bot.agentTimeoutS
    assert worst <= VOICE_WORST_S
    assert bot.asrTimeoutS < bot.agentTimeoutS


# Примечание: «печатает…» обновляется чаще, чем гаснет у MAX, и много раз за ожидание агента
def test_typingRefreshesDuringWait() -> None:
    bot, _ = defaults()
    assert bot.typingIntervalS < bot.agentTimeoutS / 2


# Примечание: лимиты полей: агенту нельзя дать ход дольше, чем ждёт бот (le у настроек)
def test_settingLimitsConsistent() -> None:
    turnLimit = AgentSettings.model_fields["turnTimeoutS"].metadata
    botDefault = BotSettings.model_fields["agentTimeoutS"].default
    assert any(getattr(m, "le", None) is not None and m.le < botDefault for m in turnLimit)
