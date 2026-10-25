"""Промпт под снапшотом и блок времени на замороженном «сейчас» (граница суток, недели, месяца)."""

import re
from datetime import UTC, datetime

from syrupy.assertion import SnapshotAssertion

from brain.prompt import PROMPT_VERSION, SYSTEM_PROMPT, buildSystemPrompt, buildTimeBlock


# Примечание: правка текста промпта видна в ревью; вместе с ней меняют PROMPT_VERSION.
# Промпт построчно: многострочная строка в .ambr даёт пробелы в конце строк, их режет pre-commit
def test_promptSnapshot(snapshot: SnapshotAssertion) -> None:
    payload = {"version": PROMPT_VERSION, "prompt": SYSTEM_PROMPT.splitlines()}
    assert payload == snapshot


# Примечание: 23:30 по Москве в субботу — «сегодня» суббота, завтра воскресенье
def test_lateEveningMoscow() -> None:
    block = buildTimeBlock(datetime(2026, 10, 24, 20, 30, tzinfo=UTC), "Europe/Moscow")
    lines = block.splitlines()
    assert lines[0] == "Сейчас: 2026-10-24T23:30+03:00 (суббота), пояс пользователя Europe/Moscow."
    assert lines[3] == "- 2026-10-24 суббота — сегодня"
    assert lines[4] == "- 2026-10-25 воскресенье — завтра"
    assert len(lines) == 3 + 14


# Примечание: 00:30 воскресенья — неделя ещё текущая (пн 19 — вс 25), следующая — с 26-го
def test_sundayWeekBoundary() -> None:
    block = buildTimeBlock(datetime(2026, 10, 24, 21, 30, tzinfo=UTC), "Europe/Moscow")
    assert block.splitlines()[1] == (
        "Эта неделя: пн 2026-10-19 — вс 2026-10-25; "
        "следующая неделя: пн 2026-10-26 — вс 2026-11-01."
    )
    assert "- 2026-10-25 воскресенье — сегодня" in block


# Примечание: конец месяца — таблица переходит на ноябрь без ошибок в днях недели
def test_endOfMonth() -> None:
    block = buildTimeBlock(datetime(2026, 10, 31, 9, 0, tzinfo=UTC), "Europe/Moscow")
    assert "- 2026-10-31 суббота — сегодня" in block
    assert "- 2026-11-02 понедельник — послезавтра" in block
    assert block.splitlines()[-1] == "- 2026-11-13 пятница"


# Примечание: в Екатеринбурге (UTC+5) в 19:30 UTC уже следующий день, чем в Москве
def test_userTimezoneDecidesToday() -> None:
    moment = datetime(2026, 10, 24, 19, 30, tzinfo=UTC)
    moscow = buildTimeBlock(moment, "Europe/Moscow")
    ekb = buildTimeBlock(moment, "Asia/Yekaterinburg")
    assert moscow.splitlines()[0].startswith("Сейчас: 2026-10-24T22:30+03:00 (суббота)")
    assert ekb.splitlines()[0].startswith("Сейчас: 2026-10-25T00:30+05:00 (воскресенье)")


# Примечание: плейсхолдеры заполнены: блок времени, фокус и pending; без контекста — «нет»
def test_systemPromptPlaceholders() -> None:
    moment = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
    empty = buildSystemPrompt(moment, "Europe/Moscow")
    assert buildTimeBlock(moment, "Europe/Moscow") in empty
    assert "Записи в фокусе (о них шла речь недавно): нет" in empty
    assert "Незавершённое действие (ждёт ответа пользователя): нет" in empty
    assert not re.search(r"\{(time_block|focus|pending)\}", empty)
    filled = buildSystemPrompt(moment, "Europe/Moscow", focus="X {pending}", pending="Y")
    # подстановка за один проход: «{pending}» в названии записи остаётся текстом
    assert "недавно): X {pending}" in filled
    assert "пользователя): Y" in filled
