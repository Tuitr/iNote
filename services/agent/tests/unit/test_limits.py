"""Пределы ответа агента (S4, шаг 4): любое название пользователя не ломает markdown MAX, текст
не длиннее 4000 символов, обрезка не рвёт экранирование.

Вместо hypothesis — набор «злых» названий и детерминированный генератор (seed): спецсимволы
разметки, эмодзи, переносы, 1000 символов, сплошные «\\» и «*».
"""

import random
from typing import Final

import pytest

from common.markdown import escapeMarkdown
from contracts.reply import REPLY_TEXT_MAX_LENGTH
from services.agent.tests.unit.test_reply_renderer import (
    TODAY,
    action,
    created,
    item,
    listed,
    render,
    turn,
)

SPECIALS: Final = frozenset("\\`*_~[]()|+")
# что escapeMarkdown экранирует: разметка и начало строки (#, >, -, точка/скобка после числа)
ESCAPABLE: Final = SPECIALS | frozenset("#>-.)")
EVIL: Final = (
    "*Срочно* [ссылка](http://x) _план_ ~~нет~~ `код` ++под++ |таблица|",
    "# заголовок",
    "> цитата",
    "- пункт",
    "1. пункт",
    "2) пункт",
    "😀👍🏽 эмодзи и 🇷🇺",
    "мульти\nстрока\tс\rтабами",
    "a" * 1000,
    "\\" * 1000,
    "*" * 3000,
    "\\*" * 1500,
    " ",
)
SEED: Final = 20261023


# Примечание: случайные названия из спецсимволов, букв и эмодзи — детерминированно по seed
def generatedTitles(count: int) -> list[str]:
    rnd = random.Random(SEED)  # noqa: S311 — не криптография
    alphabet = [*SPECIALS, "#", ">", "-", ".", "1", " ", "\n", "я", "Z", "😀"]
    return ["".join(rnd.choices(alphabet, k=rnd.randint(1, 2500))) for _ in range(count)]


# Примечание: позиции поломанной разметки: спецсимвол не после «\», «\» в конце или перед
# обычным символом (обрезка разорвала пару «\*» — MAX покажет лишний «\»)
def unescaped(text: str) -> list[int]:
    bad: list[int] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 >= len(text) or text[index + 1] not in ESCAPABLE:
                bad.append(index)
            index += 2
            continue
        if char in SPECIALS:
            bad.append(index)
        index += 1
    return bad


# Примечание: каждый markdown-ответ: ≤ 4000 символов и вся разметка экранирована
def assertSafe(reply: dict[str, object]) -> None:
    text = str(reply["text"])
    assert len(text) <= REPLY_TEXT_MAX_LENGTH
    if reply["format"] == "markdown":
        assert unescaped(text) == [], text[:200]


ALL_TITLES: Final = [*EVIL, *generatedTitles(40)]


# Примечание: подтверждение созданной записи с любым названием
# пустое название отклоняет контракт ReplyItem — для него отдельный test_emptyTitleSkipped
TITLES: Final = [title for title in ALL_TITLES if title.strip()]


@pytest.mark.parametrize("title", TITLES, ids=range(len(TITLES)))
def test_confirmAnyTitle(title: str) -> None:
    assertSafe(render(turn("done", created(item(title, "2026-10-22T10:00:00+03:00")))))


# Примечание: список и поиск из многих «злых» записей — обрезка не рвёт экранирование
@pytest.mark.parametrize("chunk", range(4))
def test_listAndSearchAnyTitles(chunk: int) -> None:
    titles = [t for t in ALL_TITLES[chunk::4] if t.strip()]
    records = [
        item(f"{n}{t}", f"2026-10-21T{10 + n % 10}:00:00+03:00", all_day=False)
        for n, t in enumerate(titles)
    ]
    assertSafe(render(turn("answer", listed(*TODAY, *records))))
    results = [{"item": r, "score": 0.5} for r in records]
    assertSafe(render(turn("answer", action("search_items", {"results": results}))))


# Примечание: escapeMarkdown сам по себе: на любом входе — без неэкранированной разметки
@pytest.mark.parametrize("title", ALL_TITLES, ids=range(len(ALL_TITLES)))
def test_escapeAlwaysSafe(title: str) -> None:
    assert unescaped(escapeMarkdown(title)) == []


# Примечание: запись с пустым названием (не по контракту) не роняет ответ — её просто нет
def test_emptyTitleSkipped() -> None:
    reply = render(turn("answer", listed(*TODAY, item("Нормальная", None), {"id": "x"})))
    assert "Нормальная" in str(reply["text"])


# Примечание: обрезка на любой позиции: длинное название из «*» разной длины — граница 4000
# приходится то между «\» и «*», то после пары; пара не рвётся ни разу
@pytest.mark.parametrize("prefix", ["", "x", "xy"])
def test_cutNeverSplitsEscape(prefix: str) -> None:
    for length in range(1995, 2005):
        title = prefix + "*" * length
        reply = render(turn("done", created(item(title, "2026-10-22T10:00:00+03:00"))))
        assertSafe(reply)
