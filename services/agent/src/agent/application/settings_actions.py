"""/settings без LLM (S3, шаг 5): экран настроек и set_tz.

Пояс — только из белого списка (domain/timezones.py), иначе кнопка не по каталогу
(validation_error в лог, «Не понял кнопку»). user_id берётся из resolve (session.user) — из
args кнопки читается только tz. Запись — SettingsWriter (J8 PATCH) со сбросом кеша:
следующий ход считает «сегодня» уже в новом поясе.
"""

from collections.abc import Mapping

from agent.application.actions import TZ_ARG
from agent.application.ports import SettingsChange, SettingsWriter
from agent.application.turn_pipeline import Session, Step, StepOutcome, TurnPipeline
from agent.domain.timezones import ruTimezone
from common.logging import getLogger
from contracts.reply import ArgValue

_log = getLogger(__name__)


# Примечание: шаги действий настроек; контекст диалога не меняют
class SettingsActions:
    # Примечание: writer — J8 PATCH настроек (infrastructure/http_user_settings.py)
    def __init__(self, pipeline: TurnPipeline, writer: SettingsWriter) -> None:
        self._pipeline = pipeline
        self._writer = writer

    # Примечание: /settings — экран с текущими значениями; args не нужны
    def settingsStep(self, _args: Mapping[str, ArgValue]) -> Step:
        # Примечание: шаг: пользователь из resolve — его пояс и голосовые ответы
        async def step(session: Session) -> StepOutcome:
            renderer = self._pipeline.deps.renderer
            return StepOutcome(renderer.settings(session.user, session.traceId))

        return step

    # Примечание: set_tz {tz}: пояс не из белого списка — None (кнопка не по каталогу)
    def setTzStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        zone = ruTimezone(args.get(TZ_ARG))
        return None if zone is None else self._change(SettingsChange(tz=zone.tz))

    # Примечание: запись через J8 → подтверждение по сохранённому (ответ D), не по кнопке
    def _change(self, change: SettingsChange) -> Step:
        # Примечание: шаг: PATCH под локом пользователя; сбой J8 — ответ с кодом от каркаса
        async def step(session: Session) -> StepOutcome:
            updated = await self._writer.update(session.user, change)
            _log.info("settings_changed", tz=change.tz)
            renderer = self._pipeline.deps.renderer
            return StepOutcome(renderer.settingsChanged(updated, session.traceId))

        return step
